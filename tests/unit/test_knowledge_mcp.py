"""百度 AI 搜索 MCP 手工探针：把 `_to_chunks` 的两条分支收敛成一条。

跑法（key 只从环境变量取，配置文件里配了也不算，免得常规回归顺手把钱花了）：

    QIANFAN_API_KEY=xxx .venv/Scripts/python.exe -m pytest tests/unit/test_knowledge_mcp.py -q -s

没显式给环境变量就整体 skip —— 这些用例要出网且按次计费，不能留在常规回归里。
第 1 条只取元数据不花钱；第 2、4 条各发一次真搜索；第 3 条默认不跑，
要对比 `model` 参数的效果再 PROBE_WITH_MODEL=1 打开（那是一次 LLM 总结，更贵）。
"""
import json
import os
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from jobagent.config.settings import settings
from jobagent.knowledge.providers.base import KnowledgeQuery
from jobagent.knowledge.providers.knowledge import TOOL_NAME, CompanyInformationMcpProvider

# 花钱的探针只认显式给的环境变量：配置文件里配了 key 不等于同意每次回归都出网计费
_KEY = os.environ.get("QIANFAN_API_KEY", "")
QUERY = "字节跳动 后端开发 待遇 怎么样"

pytestmark = pytest.mark.skipif(
    not _KEY, reason="手工探针：显式给 QIANFAN_API_KEY 环境变量才跑（要出网且按次计费）")


@asynccontextmanager
async def mcp_session():
    """自己造 httpx client：SDK 里 headers/timeout 那套旧参数已废弃且会被静默忽略。"""
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {_KEY}"},
                                 timeout=httpx.Timeout(30.0, read=300.0)) as http:
        async with streamable_http_client(settings.qianfan_mcp_url,
                                          http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                info = init.serverInfo
                print(f"\n服务端 {info.name} v{info.version} 协议 {init.protocolVersion}")
                if init.instructions:
                    print("instructions:", init.instructions[:300])
                yield session


def _dump(result) -> None:
    """把返回结构原样摊开：跑一次就知道 references 到底在哪个字段。"""
    print("isError:", result.isError)
    print("content 块类型:", [type(b).__name__ for b in result.content])
    for b in result.content:
        text = getattr(b, "text", "") or ""
        print(f"  [{type(b).__name__}] {len(text)} 字：{text[:400]}")
    sc = result.structuredContent
    print("structuredContent 顶层键:", sorted(sc.keys()) if isinstance(sc, dict) else sc)
    print("structuredContent:", json.dumps(sc, ensure_ascii=False)[:1500])


class TestCompanyInformationMcpProvider:

    async def test_01_tool_list_and_schema(self):
        """不花钱：确认工具名就叫 AIsearch，入参键名和文档一致。"""
        async with mcp_session() as session:
            tools = (await session.list_tools()).tools
            names = sorted(t.name for t in tools)
            print("工具清单:", names)
            for t in tools:
                print(f"[{t.name}] required={t.inputSchema.get('required')} "
                      f"props={sorted(t.inputSchema.get('properties', {}))}")
                print(f"    outputSchema={json.dumps(t.outputSchema, ensure_ascii=False)[:300]}")
            assert TOOL_NAME in names, f"清单里没有 {TOOL_NAME}，改 TOOL_NAME 或换端点"

    async def test_02_raw_result_shape(self):
        """一次真搜索，看基础搜索（不传 model）返回的原始形状。"""
        async with mcp_session() as session:
            result = await session.call_tool(
                TOOL_NAME, {"query": QUERY,
                            "resource_type_filter": [{"type": "web", "top_k": 3}]})
            _dump(result)
            assert not result.isError
            assert result.content or result.structuredContent, "空手而归，provider 无从取材料"

    @pytest.mark.skipif(not os.environ.get("PROBE_WITH_MODEL"),
                        reason="加一次计费调用：PROBE_WITH_MODEL=1 才跑")
    async def test_03_with_model_compare(self):
        """传了 model 之后返回长什么样 —— 用来确认「不传 model」是对的。"""
        async with mcp_session() as session:
            result = await session.call_tool(
                TOOL_NAME, {"query": QUERY, "model": os.environ["PROBE_WITH_MODEL"],
                            "resource_type_filter": [{"type": "web", "top_k": 3}]})
            _dump(result)

    async def test_04_provider_retrieve(self):
        """端到端：走 provider 自己的 _call + _to_chunks。

        state 传 None 是有意的 —— retrieve 只需要 search_query，不该碰 state。
        这条现在会红（_to_chunks 还没写完），写完它就是验收。
        """
        query = KnowledgeQuery(intent="ask_company_info", objects={"search_query": QUERY})
        chunks = await CompanyInformationMcpProvider().retrieve(query, state=None)
        print("chunks:", chunks)
        # 光断言非空会假绿：retrieve 现在无条件 return [KnowledgeChunk()]，空壳也算一条
        assert chunks and all(c.content for c in chunks), \
            "拿回来的 chunk 没有正文 —— _to_chunks 还没接上真返回形状"
        print("每条字数:", [len(c.content) for c in chunks])
