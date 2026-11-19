"""LLM JSON 调用：围栏容错、只对解析失败重试、网络/超时错误不重试。"""
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from jobagent.api.errors import AppError
from jobagent.infrastructure import llm_client as lc


def _fake_llm(reply):
    calls = {"n": 0}

    def make(_):
        calls["n"] += 1
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply)

    return RunnableLambda(make), calls


async def test_fenced_json_is_parsed_without_retry(monkeypatch):
    llm, calls = _fake_llm('```json\n{"a": 1}\n```')
    monkeypatch.setattr(lc, "get_llm", lambda: llm)
    assert await lc.ainvoke_json("{{ x }}", {"x": "hi"}) == {"a": 1}
    assert calls["n"] == 1, "围栏不该触发整轮重生成"


async def test_invalid_json_retries_once_then_fails(monkeypatch):
    llm, calls = _fake_llm("这不是 JSON")
    monkeypatch.setattr(lc, "get_llm", lambda: llm)
    with pytest.raises(AppError):
        await lc.ainvoke_json("{{ x }}", {"x": "hi"})
    assert calls["n"] == 2


async def test_timeout_is_not_retried(monkeypatch):
    llm, calls = _fake_llm(TimeoutError("slow"))
    monkeypatch.setattr(lc, "get_llm", lambda: llm)
    with pytest.raises(TimeoutError):
        await lc.ainvoke_json("{{ x }}", {"x": "hi"})
    assert calls["n"] == 1, "超时重试只会多等一轮"
