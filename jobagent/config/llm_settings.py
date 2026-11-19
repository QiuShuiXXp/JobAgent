"""模型与密钥配置的读写：Key 进凭据管理器，base_url / model 进配置文件。

桌面用户需要自己填的就是这几项。改完对当前进程立刻生效——settings 是单例，
llm_client 按新配置重建，不用重启壳和后端。
"""
import logging

from jobagent.api.errors import AppError, ErrorCode
from jobagent.config import bootstrap, paths, secrets
from jobagent.config.settings import settings

logger = logging.getLogger("jobagent.llm_settings")


def mask(key: str) -> str:
    """只留末 4 位供人辨认；没配就是空串，别让前端显示一串无意义的点。"""
    if not key:
        return ""
    return f"••••{key[-4:]}" if len(key) > 8 else "••••"


def _clean_key(raw: str, label: str) -> str:
    key = raw.strip()          # 粘贴常带首尾空白和换行
    if any(c.isspace() for c in key):
        raise AppError(ErrorCode.INVALID_ARGUMENT,
                       f"{label} 中间有空格，像是粘贴不完整", http_status=400)
    return key


def snapshot() -> dict:
    llm_key = settings.llm_api_key
    search_key = settings.qianfan_api_key
    return {
        "llm_key_set": bool(llm_key),
        "llm_key_masked": mask(llm_key),
        "llm_key_store": _store_of(secrets.LLM_KEY_NAME, llm_key),
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "search_key_set": bool(search_key),
        "search_key_masked": mask(search_key),
        "search_key_store": _store_of(secrets.QIANFAN_KEY_NAME, search_key),
        "desktop": paths.is_desktop(),
    }


def _store_of(name: str, value: str) -> str:
    """这把 Key 现在存在哪儿；没配就返回空串。只报位置，不报值。

    传进去的是配置文件里的原值而不是 settings 上已解析好的值 —— 不然从仓库 .env
    兜底来的那把会被错报成「配置文件」。
    """
    if not value:
        return ""
    return bootstrap.find_secret(name, bootstrap.config_value(name))[1]


def save(api_key: str | None = None, base_url: str | None = None,
         model: str | None = None, search_api_key: str | None = None) -> dict:
    """空 Key 表示"不改"，只改其余项；其余按非空校验后落盘。"""
    from jobagent.infrastructure.llm_client import reset_llm

    touched = False
    if api_key is not None and api_key.strip():
        key = _clean_key(api_key, "API Key")
        store = bootstrap.store_secret(secrets.LLM_KEY_NAME, key)
        logger.info("LLM 密钥已存入%s", store)
        settings.llm_api_key = key
        touched = True

    if search_api_key is not None and search_api_key.strip():
        key = _clean_key(search_api_key, "联网搜索 Key")
        store = bootstrap.store_secret(secrets.QIANFAN_KEY_NAME, key)
        logger.info("联网搜索密钥已存入%s", store)
        settings.qianfan_api_key = key       # provider 每次调用现读，立刻生效
        touched = True

    values: dict[str, str] = {}
    if base_url is not None:
        settings.llm_base_url = base_url.strip()
        values["LLM_BASE_URL"] = settings.llm_base_url
    if model and model.strip():
        settings.llm_model = model.strip()
        values["LLM_MODEL"] = settings.llm_model
    if values:
        bootstrap.upsert_config_values(values)
        touched = True

    if not touched:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "没有任何要保存的内容", http_status=400)
    reset_llm()                        # 下一次调用按新配置建模型
    return snapshot()
