"""桌面首启初始化：建运行目录、迁非密钥配置、密钥进凭据管理器。全程幂等，重跑不覆盖已有配置。

只读资源（web/ flow_config/ prompts/）留在代码目录，本模块只管可写那半边。
"""
import logging
from pathlib import Path

from dotenv import dotenv_values

from jobagent.config import paths, secrets

logger = logging.getLogger("jobagent.bootstrap")


def _dev_env_values() -> dict[str, str]:
    """仓库 .env 的原始键值（脚本要在 settings 装载前拿到它）；冻结包里没这份文件。"""
    dev_env = paths.ASSETS_ROOT / ".env"
    if not dev_env.is_file():
        return {}
    return {k: (v or "") for k, v in dotenv_values(dev_env).items()}


def _desktop_values(dev_env: dict[str, str]) -> dict[str, str]:
    """桌面 config.env 的取值：路径类由运行目录决定，密钥类一律排除。"""
    values = {k: v for k, v in dev_env.items() if k not in secrets.SECRET_NAMES}
    values.update({
        "APP_HOST": paths.DESKTOP_HOST,
        "APP_PORT": str(paths.DESKTOP_PORT),
        "DATA_BASE_URL": f"sqlite+aiosqlite:///{paths.desktop_db_file().as_posix()}",
        "STORAGE_ROOT": str(paths.desktop_storage_dir()),
        # 演练开关不继承 dev 的值：真实打招呼会在 BOSS 上建会话，宁可让用户手改一行
        "GREET_DRY_RUN": "true",
        "LOG_LEVEL": values.get("LOG_LEVEL", "INFO"),
    })
    return values


def config_value(name: str, path: Path | None = None) -> str:
    """读配置文件里的一项，默认读当前形态那份 —— 凭据管理器用不了时的回落存放处。"""
    config = path or paths.config_file()
    if not config.is_file():
        return ""
    for line in config.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip()
    return ""


def _ensure_secret(name: str, dev_value: str) -> str:
    """密钥优先落 Windows 凭据管理器；拿不到就落配置文件，绝不用空值卡住启动。

    返回一句给人看的话，里面不含任何密钥值。
    """
    if secrets.has_secret(name):
        return "凭据管理器里已有，沿用"
    if config_value(name, paths.desktop_config_file()):
        return f"凭据管理器里没有，沿用 {paths.desktop_config_file().name} 里的值"
    if not dev_value:
        return "未配置：启动后进页面在「模型设置」里填"
    if secrets.set_secret(name, dev_value):
        return "已从 .env 迁入凭据管理器"
    upsert_config_values({name: dev_value}, paths.desktop_config_file())
    return f"凭据管理器不可用，已明文写入 {paths.desktop_config_file()}"


def find_secret(name: str, config_value: str) -> tuple[str, str]:
    """密钥取值：（桌面）凭据管理器 → 当前形态的配置文件 → 仓库 .env。

    返回 (值, 来源)，来源是给人看的一个词，没配则两者都空。
    空值不算配了，继续往下找：桌面 config.env 是首启那一刻的快照，后来在仓库 .env
    里补的值不会同步过去；反过来它里面一行空占位（QIANFAN_API_KEY=）也不该把别处
    真配好的值挡住。冻结包里没有仓库 .env，最后一步自然落空。
    config_value 是 pydantic 已经合过环境变量和配置文件的那个值。
    """
    if paths.is_desktop():
        value = secrets.get_secret(name)
        if value:
            return value, "凭据管理器"
    if config_value.strip():
        return config_value.strip(), "配置文件"
    value = _dev_env_values().get(name, "").strip()
    if value:
        return value, "仓库 .env"
    return "", ""


def resolve_secret(name: str, config_value: str) -> str:
    return find_secret(name, config_value)[0]


def store_secret(name: str, value: str) -> str:
    """落密钥：桌面进凭据管理器，dev（或 keyring 不可用）明文进当前配置文件。

    返回存放处名称，给前端显示用；调用方自己保证 value 非空。
    """
    if paths.is_desktop() and secrets.set_secret(name, value):
        return "凭据管理器"
    upsert_config_values({name: value})
    logger.info("%s 已写入配置文件 %s", name, paths.config_file())
    return "配置文件"


def upsert_config_values(values: dict[str, str], path: Path | None = None) -> Path:
    """把几个键写进配置文件：已有的就地替换，缺的追加，注释和顺序保留。

    默认写当前形态的配置文件；初始化桌面目录时要显式指到桌面那份（在 dev 里也要写它）。
    """
    path = path or paths.config_file()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    left = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in left:
            lines[i] = f"{key}={left.pop(key)}"
    lines += [f"{k}={v}" for k, v in left.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def init_desktop(migrate_llm_key: bool = True) -> dict[str, str]:
    """返回迁移报告，值全是给人看的一句话，不含任何密钥。"""
    home = paths.ensure_desktop_home()
    config = paths.desktop_config_file()
    dev_env = _dev_env_values()
    report = {"运行目录": str(home)}

    if config.exists():
        report["config.env"] = f"已存在，未覆盖：{config}"
    else:
        lines = ["# JobAgent 桌面配置（明文，只放非密钥项；密钥优先在 Windows 凭据管理器）",
                 "# 首启自动生成，可手改。",
                 "# BOSS 页面跑在应用自己内嵌的浏览器里，不用装也不用配浏览器；"
                 "BROWSER_PATH 只在仓库里跑 dev 形态（自己拉 Chrome）时才用得上。"]
        lines += [f"{k}={v}" for k, v in _desktop_values(dev_env).items()]
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["config.env"] = f"已写入 {config}（GREET_DRY_RUN=true）"

    report["LLM_API_KEY"] = _ensure_secret(
        secrets.LLM_KEY_NAME, dev_env.get(secrets.LLM_KEY_NAME, "") if migrate_llm_key else "")
    return report


def ensure_desktop_ready() -> dict[str, str] | None:
    """桌面形态启动前自愈：运行目录、config.env 缺哪样补哪样。

    settings.py 必须在构造 Settings() 之前调它：全新机器上 env_file 指向的 config.env
    还不存在，晚一步这个进程就读不到任何配置，双击 exe 起不来。
    LLM key 不从仓库 .env 迁 —— 静默搬走用户的密钥不合适，缺了就让页面去问。
    """
    if not paths.is_desktop():
        return None
    return init_desktop(migrate_llm_key=False)
