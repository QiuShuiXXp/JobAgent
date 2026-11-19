"""密钥存取：桌面形态放 Windows 凭据管理器，dev 形态仍从仓库 .env 读。

本机自用形态：密钥只出现在当前用户的凭据管理器里，config.env 是可随便看的纯文本。
keyring 后端不可用时（比如装在服务账号下）降级为读配置文件，并打一条 warning 提醒。
"""
import logging

logger = logging.getLogger("jobagent.secrets")

SERVICE = "JobAgent"

LLM_KEY_NAME = "LLM_API_KEY"
QIANFAN_KEY_NAME = "QIANFAN_API_KEY"   # 联网搜索（百度千帆 MCP）

# 所有算密钥的配置项：桌面 config.env 里不留明文，取值走 bootstrap.resolve_secret
SECRET_NAMES = (LLM_KEY_NAME, QIANFAN_KEY_NAME)


def _backend():
    """返回可用 keyring 后端，拿不到就返回 None（调用方回落配置文件）。"""
    try:
        import keyring
    except ImportError:
        return None
    try:
        keyring.get_password(SERVICE, "probe")
    except Exception as exc:
        logger.warning("凭据管理器不可用，回落到配置文件：%r", exc)
        return None
    return keyring


def get_secret(name: str, fallback: str | None = None) -> str | None:
    ring = _backend()
    if ring is not None:
        value = ring.get_password(SERVICE, name)
        if value:
            return value
    return fallback or None


def set_secret(name: str, value: str) -> bool:
    """写入凭据管理器；后端不可用时返回 False，让调用方决定怎么提示。"""
    ring = _backend()
    if ring is None:
        return False
    ring.set_password(SERVICE, name, value)
    return True


def has_secret(name: str) -> bool:
    return get_secret(name) is not None
