"""解析预算语义回归：180s 是覆盖全部重试轮次的总预算，不是单轮。

背景（2026-11-18 实测）：spark-x2.6 真实简历体量单轮 60-90s，旧实现把 wait_for
套在单轮上，传输重试/schema 修复一叠就必然 2002；样例文件单轮干净所以测不出来。
"""
import asyncio

import pytest

import jobagent.infrastructure.llm_client as llm_client
import jobagent.services.resume_service as rs
from jobagent.api.errors import AppError, ErrorCode
from jobagent.services.resume_service import ResumeService


def _svc() -> ResumeService:
    return ResumeService(file_store=None, extract_template="{{ resume_text }}")


async def test_timeout_is_total_across_repair_rounds(monkeypatch):
    """两轮各 0.2s、总预算 0.3s：旧的单轮预算会放行，总预算必须拦下。"""
    monkeypatch.setattr(rs, "PARSE_TIMEOUT_S", 0.3)
    calls = []

    async def fake(template_text, inputs):
        calls.append(1)
        await asyncio.sleep(0.2)
        return {"name": "x", "experiences": "not-a-list"}   # 每轮都触发修复重试

    monkeypatch.setattr(llm_client, "ainvoke_json", fake)
    with pytest.raises(AppError) as e:
        await _svc().parse("text")
    assert e.value.error is ErrorCode.RESUME_PARSE_TIMEOUT
    assert len(calls) == 2


async def test_single_slow_round_over_budget_raises_2002(monkeypatch):
    monkeypatch.setattr(rs, "PARSE_TIMEOUT_S", 0.2)

    async def fake(template_text, inputs):
        await asyncio.sleep(1)
        return {}

    monkeypatch.setattr(llm_client, "ainvoke_json", fake)
    with pytest.raises(AppError) as e:
        await _svc().parse("text")
    assert e.value.error is ErrorCode.RESUME_PARSE_TIMEOUT


async def test_schema_repair_retry_succeeds_within_budget(monkeypatch):
    monkeypatch.setattr(rs, "PARSE_TIMEOUT_S", 5)
    replies = [{"name": "x", "experiences": "not-a-list"}, {"name": "x"}]

    async def fake(template_text, inputs):
        return replies.pop(0)

    monkeypatch.setattr(llm_client, "ainvoke_json", fake)
    summary = await _svc().parse("text")
    assert summary["name"] == "x"
    assert replies == []