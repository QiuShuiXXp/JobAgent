"""运行目录分家：只读资源跟着代码走，可写数据放用户目录。

dev（在仓库里跑）：读写都留在仓库内，行为与改造前一致，不碰 %LOCALAPPDATA%。
桌面（打包 exe / Tauri 壳 / 显式设 JOBAGENT_HOME）：可写数据统一落 %LOCALAPPDATA%\\JobAgent\\
    config.env   非密钥配置
    jobagent.db  SQLite
    storage\\     简历文件 + 每用户 chrome_profile
    logs\\        滚动日志（窗口形态没有控制台，出问题只能看文件）
密钥不放这里，见 secrets.py（Windows 凭据管理器）。
"""
import os
import sys
from pathlib import Path

# web/ flow_config/ prompts/ 这类随程序分发的只读资源在这
def _assets_root() -> Path:
    """只读资源根：dev 是仓库根；冻结后是解包目录（--add-data 的东西落在这下面）。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


ASSETS_ROOT = _assets_root()
APP_NAME = "JobAgent"

# 桌面壳只按这一个端口连后端；desktop/ 的 Tauri 配置里要写同一个值
DESKTOP_HOST = "127.0.0.1"
DESKTOP_PORT = 8712


def _default_home() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / APP_NAME


def is_desktop() -> bool:
    """形态判定。JOBAGENT_DESKTOP 显式设 false 可在装过桌面版的机器上强行跑 dev。"""
    flag = os.environ.get("JOBAGENT_DESKTOP")
    if flag is not None:
        return flag.strip().lower() not in ("0", "false", "no", "")
    if os.environ.get("JOBAGENT_HOME") or getattr(sys, "frozen", False):
        return True
    return (_default_home() / "config.env").is_file()


def desktop_home() -> Path:
    """桌面运行目录，与当前形态无关 —— 首启初始化脚本在 dev 里也要能写它。"""
    return Path(os.environ["JOBAGENT_HOME"]) if os.environ.get("JOBAGENT_HOME") else _default_home()


def desktop_config_file() -> Path:
    return desktop_home() / "config.env"


def desktop_db_file() -> Path:
    return desktop_home() / "jobagent.db"


def desktop_storage_dir() -> Path:
    return desktop_home() / "storage"


def desktop_log_dir() -> Path:
    return desktop_home() / "logs"


def ensure_desktop_home() -> Path:
    for path in (desktop_home(), desktop_storage_dir(), desktop_log_dir()):
        path.mkdir(parents=True, exist_ok=True)
    return desktop_home()


def app_home() -> Path:
    """可写数据根；dev 时就是仓库根，沿用 .env + ./data 的历史布局。"""
    return desktop_home() if is_desktop() else ASSETS_ROOT


def config_file() -> Path:
    return desktop_config_file() if is_desktop() else ASSETS_ROOT / ".env"


def storage_dir() -> Path:
    return desktop_storage_dir() if is_desktop() else app_home() / "storage"


def log_dir() -> Path:
    return desktop_log_dir() if is_desktop() else app_home() / "logs"


def db_file() -> Path:
    return desktop_db_file() if is_desktop() else app_home() / "jobagent.db"


def lock_file() -> Path:
    """单实例锁：跟着可写数据走，一个运行目录只允许一个后端。"""
    return app_home() / ".lock"


# 内置浏览器目录名（Chrome for Testing 解压后的目录名）
BROWSER_DIR = "chrome-win64"


def bundled_browser() -> Path:
    """仓库里那份 Chrome for Testing 的可执行文件路径，不管存不存在。

    在 desktop/vendor/chrome-win64/（scripts/fetch_browser.py 拉下来的），只有 dev 形态
    （在普通浏览器里开 localhost，后端自己拉一个 Chrome）用得上：DrissionPage 只认
    chrome.exe，对方电脑多半没装，仓库里备一份省心得多。
    桌面分发包不再带浏览器 —— BOSS 页面跑在壳内嵌的 WebView2 里。
    """
    return (ASSETS_ROOT / "desktop" / "vendor" / BROWSER_DIR / "chrome.exe")


def env_port(name: str) -> int | None:
    """桌面壳传下来的端口号；没传（dev 形态）返回 None。"""
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else None


def ensure_app_home() -> Path:
    """建出桌面运行目录；dev 直接返回仓库根，什么都不建。"""
    if not is_desktop():
        return ASSETS_ROOT
    return ensure_desktop_home()
