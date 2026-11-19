//! 桌面壳：拉起 FastAPI 后端，开一个窗口装两块 webview —— 右聊天、左 BOSS 浏览器。
//!
//! BOSS 栏是内嵌的 WebView2，后端用 CDP（调试端口）直接接管它，不再自己拉 Chrome。
//! 平时这块摆在窗口客户区外面（看不见但仍在渲染），要登录时后端往控制端口发一行
//! `reveal`，它就占住左半、聊天缩到右半；用户也能经聊天页按钮发 `collapse` 收起。

use std::io::{BufRead, BufReader, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use tauri::webview::WebviewBuilder;
use tauri::{
    AppHandle, LogicalPosition, LogicalSize, Manager, WebviewUrl, Window, WindowEvent,
};

/// 与 jobagent/config/paths.py 的 DESKTOP_HOST/DESKTOP_PORT 是同一个值，改一边要改另一边
const HOST: &str = "127.0.0.1";
const PORT: u16 = 8712;
/// BOSS 栏的调试端口与控制端口默认每次现挑：写死会和用户机器上的软件、
/// 同机另一个登录会话抢同一个端口。JOBAGENT_CDP_PORT / JOBAGENT_SHELL_CTL_PORT 可钉住。
const CDP_PORT_ENV: &str = "JOBAGENT_CDP_PORT";
const CTL_PORT_ENV: &str = "JOBAGENT_SHELL_CTL_PORT";
/// 后端要 import langchain、建引擎、建表，冷启动实测 7~9s
const READY_TIMEOUT: Duration = Duration::from_secs(60);
const APP_TITLE: &str = "JobAgent";
const W: f64 = 1280.0;
const H: f64 = 860.0;
/// BOSS 的响应式断点是 1024 CSS px，贴合到这个宽度以上才有「其他登录方式」，留点余量
const BOSS_MIN_CSS: f64 = 1080.0;
/// 缩放的下限：再小字就糊了，宁可退回窄版
const BOSS_MIN_ZOOM: f64 = 0.6;

/// 后端与壳之间约定的两个端口，随环境变，所以不能当常量用
struct Ports {
    /// BOSS 栏的 WebView2 调试端口，后端 Chromium("127.0.0.1:这里") 接管它
    cdp: u16,
    /// 壳的几何控制端口，后端发一行指令（reveal / state）
    ctl: u16,
}

fn env_port(name: &str) -> Option<u16> {
    std::env::var(name).ok()?.trim().parse().ok()
}

/// 占一个端口再还回去，只为拿个大概率没人用的号
fn free_port() -> u16 {
    TcpListener::bind((IpAddr::V4(Ipv4Addr::LOCALHOST), 0))
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(0)
}

/// 左栏是否展开。登录/风控置 true，用户经聊天页按钮可双向切换
struct Revealed(AtomicBool);

/// 我们拉起的后端进程；None 表示端口本来就被别人占着（比如 dev 服务器还在跑），不归壳管
struct Backend(Mutex<Option<Child>>);

fn repo_dir() -> PathBuf {
    if let Ok(p) = std::env::var("JOBAGENT_REPO") {
        return PathBuf::from(p);
    }
    // 开发壳的 exe 在 <仓库>/desktop/src-tauri/target/<profile>/ 下，向上找仓库根
    if let Ok(exe) = std::env::current_exe() {
        for dir in exe.ancestors().skip(1) {
            if dir.join("pyproject.toml").is_file() {
                return dir.to_path_buf();
            }
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn python_exe(repo: &PathBuf) -> PathBuf {
    if let Ok(p) = std::env::var("JOBAGENT_PYTHON") {
        return PathBuf::from(p);
    }
    repo.join(".venv").join("Scripts").join("python.exe")
}

/// BOSS 栏的登录态就存在这里，删了等于退出登录
fn boss_data_dir() -> PathBuf {
    std::env::var("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| std::env::temp_dir())
        .join("JobAgent")
        .join("webview2_boss")
}

fn port_open(port: u16) -> bool {
    let a = SocketAddr::new(IpAddr::V4(Ipv4Addr::new(127, 0, 0, 1)), port);
    TcpStream::connect_timeout(&a, Duration::from_millis(400)).is_ok()
}

/// 后端从哪来：分发包里是 exe 同级的冻结后端，开发机上仍是仓库 .venv
enum BackendSource {
    Frozen(PathBuf),
    Dev { repo: PathBuf },
}

fn find_backend() -> BackendSource {
    if let Ok(p) = std::env::var("JOBAGENT_BACKEND") {
        return BackendSource::Frozen(PathBuf::from(p));
    }
    // 绿色分发包的约定布局：<任意目录>/JobAgent/JobAgent.exe + JobAgent/jobagent-backend/jobagent-backend.exe
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidate = dir.join("jobagent-backend").join("jobagent-backend.exe");
            if candidate.is_file() {
                return BackendSource::Frozen(candidate);
            }
        }
    }
    BackendSource::Dev { repo: repo_dir() }
}

fn spawn_backend(source: &BackendSource, ports: &Ports) -> Result<Child, String> {
    let mut cmd = match source {
        BackendSource::Frozen(path) => {
            let mut c = Command::new(path);
            c.current_dir(path.parent().ok_or("冻结后端路径异常")?);
            c
        }
        BackendSource::Dev { repo } => {
            let py = python_exe(repo);
            if !py.exists() {
                return Err(format!(
                    "找不到虚拟环境 python：{}（先在仓库根跑 uv sync，或改用打包好的 JobAgent 目录）",
                    py.display()
                ));
            }
            // cwd 用仓库根、模块写全路径 jobagent.api.app:app：分家之后的路径全是绝对的，
            // 不再依赖启动时的当前目录
            let mut c = Command::new(&py);
            c.current_dir(repo).args([
                "-m", "uvicorn", "jobagent.api.app:app",
                "--host", HOST, "--port", &PORT.to_string(),
            ]);
            c
        }
    };
    cmd.env("JOBAGENT_DESKTOP", "1")
        .env(CDP_PORT_ENV, ports.cdp.to_string())
        .env(CTL_PORT_ENV, ports.ctl.to_string())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // 冻结后端是控制台程序，不挡掉会跟着弹一个黑框
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn().map_err(|e| match source {
        BackendSource::Frozen(path) => format!("启动后端失败（{}）：{e}", path.display()),
        BackendSource::Dev { repo } => format!("启动后端失败（{}）：{e}", repo.display()),
    })
}

fn wait_until_ready() -> bool {
    let deadline = Instant::now() + READY_TIMEOUT;
    while Instant::now() < deadline {
        if port_open(PORT) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    false
}

/// 关窗即收尾：只杀自己拉起的那棵进程树
fn shutdown_backend(state: &Backend) {
    if let Some(mut child) = state.0.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// 摆两块 webview。BOSS 栏收起时挪到客户区外，不用 hide()：
/// hide 之后 rAF/scroll/IntersectionObserver 全停，靠滚动触发的岗位翻页会失效（实测见 spike）。
fn apply_layout(win: &Window, revealed: bool) {
    let (Some(boss), Some(chat)) = (win.get_webview("boss"), win.get_webview("chat")) else {
        return;
    };
    let (Ok(scale), Ok(size)) = (win.scale_factor(), win.inner_size()) else {
        return;
    };
    let (w, h) = (size.width as f64 / scale, size.height as f64 / scale);
    let half = w / 2.0;
    let set = |v: &tauri::Webview, x: f64, y: f64, pw: f64, ph: f64| {
        let _ = v.set_size(LogicalSize::new(pw, ph));
        let _ = v.set_position(LogicalPosition::new(x, y));
    };
    if revealed {
        set(&boss, 0.0, 0.0, half, h);
        set(&chat, half, 0.0, w - half, h);
    } else {
        set(&chat, 0.0, 0.0, w, h);
        set(&boss, -half - 8.0, 0.0, half, h);
    }
    // BOSS 自己的断点是 1024 CSS px：窄于此，登录页会塌成只有手机号+验证码的窄版，
    // 「APP 扫码 / 密码 / 其他登录方式」那个入口直接 display:none（实测 .btn-sign-switch 宽 0）。
    // 缩放和浏览器缩放同理：布局视口 = 逻辑宽 / zoom，所以反算出保证 ≥BOSS_MIN_CSS 的倍率。
    // 用户仍可用 Ctrl+滚轮 自己调；改窗口尺寸会重新按可用宽度贴合。
    let zoom = (half / BOSS_MIN_CSS).clamp(BOSS_MIN_ZOOM, 1.0);
    let _ = boss.set_zoom(zoom);
}

fn set_revealed(app: &AppHandle, revealed: bool) {
    app.state::<Revealed>().0.store(revealed, Ordering::Relaxed);
    if let Some(win) = app.get_window("main") {
        apply_layout(&win, revealed);
    }
}

/// 后端 → 壳的唯一通道：一行文本指令。绑定失败不影响聊天能用，只是左栏展不开。
fn serve(listener: TcpListener, app: AppHandle) {
    for stream in listener.incoming().flatten() {
        let mut line = String::new();
        if BufReader::new(&stream).read_line(&mut line).is_err() {
            continue;
        }
        let reply = match line.trim() {
            "reveal" => {
                set_revealed(&app, true);
                "ok"
            }
            "collapse" => {
                set_revealed(&app, false);
                "ok"
            }
            "state" => {
                if app.state::<Revealed>().0.load(Ordering::Relaxed) {
                    "revealed"
                } else {
                    "collapsed"
                }
            }
            other => &format!("ERR unknown {other}"),
        };
        let _ = (&stream).write_all(format!("{reply}\n").as_bytes());
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend = find_backend();

    let app = tauri::Builder::default()
        // 二次启动只把已有窗口拉到前台，本进程当场退出——不能让它走到 setup 去拉第二个后端
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .on_window_event(|win, event| {
            if win.label() == "main" {
                if let WindowEvent::Resized(_) = event {
                    let revealed = win.state::<Revealed>().0.load(Ordering::Relaxed);
                    apply_layout(win, revealed);
                }
            }
        })
        .setup(move |app| {
            // 控制端口现在就占住（顺带拿到号），调试端口只要个空闲号交给 WebView2 去 bind
            let ctl_listener =
                TcpListener::bind((IpAddr::V4(Ipv4Addr::LOCALHOST), env_port(CTL_PORT_ENV).unwrap_or(0)))?;
            let ports = Ports {
                cdp: env_port(CDP_PORT_ENV).unwrap_or_else(free_port),
                ctl: ctl_listener.local_addr()?.port(),
            };
            log::info!("BOSS 栏调试端口 {}，壳控制端口 {}", ports.cdp, ports.ctl);

            // 端口已经通了 = 后面早就在跑（多半是 dev 服务器），壳只附身不接管
            let owned_child = if port_open(PORT) {
                log::info!("端口 {PORT} 已被占用，壳直接附加到现有后端");
                None
            } else {
                Some(spawn_backend(&backend, &ports).map_err(|e| {
                    log::error!("{e}");
                    e
                })?)
            };
            app.manage(Backend(Mutex::new(owned_child)));
            app.manage(Revealed(AtomicBool::new(false)));

            let url = if wait_until_ready() {
                format!("http://{HOST}:{PORT}/")
            } else {
                log::error!("{READY_TIMEOUT:?} 内后端端口没通");
                // 起不来就开本地兜底页，让用户看得到原因而不是对着白屏
                String::from("index.html")
            };
            let chat_url = if url.starts_with("http") {
                WebviewUrl::External(url.parse().unwrap())
            } else {
                WebviewUrl::App(url.into())
            };

            let window = tauri::Window::builder(app, "main")
                .title(APP_TITLE)
                .inner_size(W, H)
                .build()?;

            // 两块 webview 的浏览器参数不同 → WebView2 起两个独立进程，
            // 所以调试端口上只有 BOSS 栏，看不到聊天页
            let browser_args = format!(
                "--remote-debugging-port={} \
                 --disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection",
                ports.cdp
            );
            window.add_child(
                WebviewBuilder::new("boss", WebviewUrl::External("about:blank".parse().unwrap()))
                    .data_directory(boss_data_dir())
                    // 默认关掉的话 Ctrl+滚轮和 Ctrl+-/= 在 WebView2 里都不生效，用户没法自己放大
                    .zoom_hotkeys_enabled(true)
                    .additional_browser_args(&browser_args),
                LogicalPosition::new(0.0, 0.0),
                LogicalSize::new(W / 2.0, H),
            )?;
            window.add_child(
                WebviewBuilder::new("chat", chat_url),
                LogicalPosition::new(0.0, 0.0),
                LogicalSize::new(W, H),
            )?;
            apply_layout(&window, false);
            window.set_focus().ok();

            let handle = app.handle().clone();
            std::thread::spawn(move || serve(ctl_listener, handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(state) = handle.try_state::<Backend>() {
                shutdown_backend(&state);
            }
        }
    });
}
