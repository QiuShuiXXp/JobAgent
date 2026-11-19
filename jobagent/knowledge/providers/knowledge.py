import logging

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from jobagent.config.paths import config_file
from jobagent.config.settings import settings
from jobagent.domain.state import DialogueState
from jobagent.knowledge.providers.base import KnowledgeChunk, KnowledgeQuery

log = logging.getLogger(__name__)

TOOL_NAME = "chatCompletions"
TOP_K = 3
# 服务端把失败当正文回，不当错误：实测 isError 为 False 时也可能只有一句提示文本
EMPTY_TEXTS = ("查询失败", "没有查询到", "未搜索到")


class CompanyInformationMcpProvider:
    """
    百度 AI 搜索：只取材料，不答题。
    """

    async def retrieve(self, knowledge_query: KnowledgeQuery, state: DialogueState) -> list[KnowledgeChunk]:

        search_query = knowledge_query.objects.get("search_query", "").strip()
        if not search_query:
            return []
        # key 没配就直接收手：不拦的话 Authorization 头是 "Bearer "，
        # httpx 会抛 LocalProtocolError，被 anyio 包成 ExceptionGroup，
        # 日志里看像网络故障，其实差一行配置
        if not settings.qianfan_api_key:
            log.warning("未配置 QIANFAN_API_KEY，跳过联网搜索：query=%s"
                        "（已找过凭据管理器和 %s，桌面版可在页面「模型设置」里补）",
                        search_query, config_file())
            return []

        # 联网搜索
        try:
            web_search_result = await self._call(
                {
                    "query": search_query,
                    "resource_type_filter": [{"type": "web", "top_k": TOP_K}]
                }
            )
        except Exception:
            # 系统边界：key 缺失/断网/工具名错都到这儿。让它穿到引擎就变成 500（错误码 1005），
            # 这轮回"没查到"就够，别拖垮整轮。anyio 会把真异常包成 ExceptionGroup，
            # 所以必须 log.exception 才看得到嵌在里面那条成因。
            log.exception("联网搜索失败 query=%s", search_query)
            return []
        # 转成KnowledgeChunk
        return await self._to_chunks(web_search_result)

    @staticmethod
    async def _call(arguments: dict):
        async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {settings.qianfan_api_key}"},
                timeout=httpx.Timeout(30.0, read=300.0)) as http:
            async with streamable_http_client(settings.qianfan_mcp_url,
                                              http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool(TOOL_NAME, arguments)


    @staticmethod
    async def _to_chunks(result) -> list[KnowledgeChunk]:
        if result is None or result.isError:
            return []
        text = "\n".join(getattr(b, "text", "") or "" for b in result.content
                         if getattr(b, "type", "") == "text").strip()
        if not text or text.startswith(EMPTY_TEXTS):
            return []
        return [KnowledgeChunk(content=text)]
