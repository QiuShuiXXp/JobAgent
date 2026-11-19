"""一条命令出分发包：冻结后端 → 编译壳 → 组装绿色目录 → 压 zip。

    uv run python scripts/make_dist.py                # 全量
    uv run python scripts/make_dist.py --skip-backend # 复用已冻结的后端（改壳时快）
    uv run python scripts/make_dist.py --skip-shell   # 复用已编译的壳（调后端时快）

产物是 <仓库>/desktop/out/JobAgent/ 和 JobAgent-win64-<日期>.zip，
对方解压到任意目录，双击 JobAgent.exe 即可 —— 不需要 Python、不需要装浏览器、不需要跑脚本。
BOSS 页面跑在壳内嵌的 WebView2 里（和聊天页同一个运行时），所以包里不再带 Chrome。
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "desktop" / "out"
STAGE = OUT / "JobAgent"
BACKEND_DIST = ROOT / "desktop" / "dist" / "jobagent-backend"
SHELL_EXE = ROOT / "desktop" / "src-tauri" / "target" / "release" / "app.exe"
for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")

# 这些名字一旦进包就是事故：密钥、真实登录态、用户简历文件
FORBIDDEN = {".env", "config.env", "jobagent.db", "chrome_profile", "storage",
             "resumes", "jobagent.log", ".lock", "pyvenv.cfg"}
# 只读资源是 --add-data 进冻结包的，路径以 _internal 为根，别按仓库布局写
REQUIRED = ["jobagent-backend/_internal/web/index.html",
            "jobagent-backend/_internal/web/chat.js",
            "jobagent-backend/_internal/web/vendor/vue.global.prod.js",
            "jobagent-backend/_internal/flow_config/user_flows.yml",
            "jobagent-backend/_internal/flow_config/system_flows.yml",
            "jobagent-backend/_internal/jobagent/prompts/jinja2/greet_copy.j2",
            "jobagent-backend/jobagent-backend.exe"]
README = """JobAgent 求职副驾 —— 使用说明

【怎么跑】
1. 把整个 JobAgent 文件夹解压到任意位置（桌面、D 盘都行，别解压成压缩包里的临时视图）。
2. 双击 JobAgent.exe。第一次启动要 10~20 秒：它自己建运行目录和配置文件。
3. 页面右上角是「配置模型」时，点它填三项：API Key、Base URL、模型名。填完就能聊。
   Key 存在 Windows 凭据管理器里，不写进任何文件；换模型不用重装。

【需要什么】
- Windows 10 64 位 / 11，64 位。
- Microsoft Edge WebView2 运行时：聊天界面和 BOSS直聘 页面都跑在它上面。Win11 和更新过的
  Win10 自带；万一提示缺，搜「Microsoft Edge WebView2 Runtime」装一个即可。
  不用装任何浏览器，也不碰你自己的 Chrome/Edge —— 登录态只存在本应用自己的目录里。

【数据在哪】
C:\\Users\\<你>\\AppData\\Local\\JobAgent\\
  config.env    非密钥配置（可手改）
  jobagent.db   会话与任务记录
  storage\\      简历文件
  webview2_boss\\ BOSS直聘 那半屏的登录态（删掉它等于退出登录）
  logs\\         出问题先看 jobagent.log

【默认不会真的发消息】
config.env 里 GREET_DRY_RUN=true：打招呼只走到岗位详情页并定位「立即沟通」按钮，
不点、不发消息。确认行为没问题后自己把那行改成 false。

【其他】
- 一台机器同时只能开一个（固定端口 127.0.0.1:8712 + 单实例锁）。再点图标只会把已有窗口带到前台。
- 程序没做代码签名，第一次打开 Windows 可能提示「未知发布者」，点「更多信息 → 仍要运行」。
- 页面报「错误 5004」= 连不上内嵌的 BOSS 页面（多半是 WebView2 运行时缺失，或安全软件拦了
  本机回环端口）；把 logs\\jobagent.log 里 「浏览器形态」那行发给作者就行。
- 卸载 = 删掉解压的文件夹；想连数据一起清就再删上面那个 AppData\\Local\\JobAgent。
"""


def run(cmd, cwd=None) -> None:
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd or ROOT),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print((r.stdout or "")[-3000:])
        raise SystemExit(f"命令失败，退出码 {r.returncode}")


def cargo() -> str:
    found = shutil.which("cargo")
    if found:
        return found
    for base in (Path.home() / ".cargo" / "bin",
                 Path(sys.prefix) / "bin"):
        exe = base / "cargo.exe"
        if exe.is_file():
            return str(exe)
    raise SystemExit("找不到 cargo：先装 Rust 工具链（或在 PATH 里能搜到 cargo.exe）")


def build_backend() -> None:
    print("\n[1/4] 冻结后端（PyInstaller onedir）")
    run([sys.executable, "-m", "PyInstaller", "desktop/backend.spec", "--noconfirm",
         "--distpath", ROOT / "desktop" / "dist", "--workpath", ROOT / "desktop" / "build"])
    if not (BACKEND_DIST / "jobagent-backend.exe").is_file():
        raise SystemExit(f"冻结完没找到 {BACKEND_DIST}")


def build_shell() -> None:
    print("\n[2/4] 编译桌面壳（cargo --release）")
    run([cargo(), "build", "--release"], cwd=ROOT / "desktop" / "src-tauri")
    if not SHELL_EXE.is_file():
        raise SystemExit(f"编译完没找到 {SHELL_EXE}")


def scan(path: Path) -> list[str]:
    bad = []
    for p in path.rglob("*"):
        low = p.name.lower()
        if low in FORBIDDEN or low.endswith(".env"):
            bad.append(str(p.relative_to(path)))
    return bad


def assemble() -> None:
    print("\n[3/4] 组装绿色目录")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    shutil.copytree(BACKEND_DIST, STAGE / "jobagent-backend")
    shutil.copy2(SHELL_EXE, STAGE / "JobAgent.exe")
    (STAGE / "说明.txt").write_text(README, encoding="utf-8")

    # 内置 Chrome 已不再打进包（BOSS 页面走壳内嵌的 WebView2），敏感文件扫描照旧
    dirty = scan(STAGE)
    if dirty:
        raise SystemExit(f"分发包里有不该出现的东西，不发：\n  " + "\n  ".join(dirty))
    missing = [r for r in REQUIRED if not (STAGE / r).exists()]
    if missing:
        raise SystemExit(f"分发包缺文件，不发：{missing}")
    size = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file()) / 1e6
    print(f"  组装完成：{STAGE}  共 {size:.0f} MB，敏感文件扫描通过")


def zip_out() -> Path:
    print("\n[4/4] 压 zip")
    # 时间戳到分钟：同一天重出包不会覆盖已经发给别人的那个 zip
    stamp = time.strftime("%Y%m%d-%H%M")
    target = OUT / f"JobAgent-win64-{stamp}.zip"
    if target.exists():
        target.unlink()
    archive = shutil.make_archive(str(target.with_suffix("")), "zip", root_dir=OUT,
                                  base_dir="JobAgent")
    data = Path(archive).read_bytes()
    print(f"  {archive}")
    print(f"  体积 {len(data)/1e6:.0f} MB   sha256 {hashlib.sha256(data).hexdigest()[:16]}")
    return Path(archive)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-backend", action="store_true")
    ap.add_argument("--skip-shell", action="store_true")
    ap.add_argument("--no-zip", action="store_true")
    args = ap.parse_args()

    if not args.skip_backend:
        build_backend()
    if not args.skip_shell:
        build_shell()
    assemble()
    if not args.no_zip:
        zip_out()
    print("\n发这个目录/压缩包给对方就行；他自己只要解压 + 双击。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
