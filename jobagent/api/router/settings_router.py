"""路由：/api/settings —— 桌面用户接上自己的模型（首启没 Key 时页面靠它引导）。"""
from fastapi import APIRouter

from jobagent.api.schemas import LlmSettingsIn, LlmSettingsOut
from jobagent.config import llm_settings

router = APIRouter()


@router.get("/api/settings", response_model=LlmSettingsOut)
async def get_settings() -> LlmSettingsOut:
    return LlmSettingsOut(**llm_settings.snapshot())


@router.put("/api/settings/llm", response_model=LlmSettingsOut)
async def put_llm_settings(req: LlmSettingsIn) -> LlmSettingsOut:
    """保存后立即生效：settings 单例改值 + llm_client 重建，不重启进程。"""
    return LlmSettingsOut(**llm_settings.save(
        api_key=req.api_key, base_url=req.base_url, model=req.model,
        search_api_key=req.search_api_key))
