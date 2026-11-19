"""LLM 打分：单次调用批量打分、解析失败重试一次、再失败分数留空不入列。

岗位字段名必须和 action_search_jobs._normalize_job 产出的对得上。
"""
import json

import pytest

import jobagent.matching.llm as llm_mod
from jobagent.api.errors import AppError, ErrorCode
from jobagent.matching.llm import LlmMatchProvider
from jobagent.services.match_service import MAX_CANDIDATES, MatchService

# 就是 _normalize_job 的产出形状（BOSS joblist.json 的字段）
JOB = {
    "job_id": "j1", "title": "Python 后端工程师", "city": "郑州", "district": "金水区",
    "company": "某数据服务公司", "salary_text": "20-30K", "experience": "3-5年",
    "degree": "本科", "skills": ["Python", "MySQL", "Redis"], "welfare": ["五险一金"],
    "link": "https://www.zhipin.com/job_detail/j1.html",
}
RESUME = {"skills": ["Python"], "work_years": 4, "intent_cities": "郑州",
          "contact": "15500000000", "completeness": {"missing": []}}
TEMPLATE = "{{ resume_json }} {{ jobs_json }}"


def _job(jid: str, **over) -> dict:
    return {**JOB, "job_id": jid, **over}


def _provider(monkeypatch, replies: list):
    """打桩 ainvoke_json：记录每次调用的 inputs，按脚本回输出。"""
    calls = []

    async def fake(template_text, inputs):
        calls.append(inputs)
        return replies.pop(0)

    monkeypatch.setattr(llm_mod, "ainvoke_json", fake)
    return LlmMatchProvider(TEMPLATE), calls


def _sheet(jobs: list[dict], score: float = 0.8) -> dict:
    return {"scores": [{"job_id": j["job_id"], "score": score, "reasons": ["命中"]}
                       for j in jobs]}


async def test_one_call_scores_all_jobs(monkeypatch):
    """15 个岗位一次调用：上传时存好的摘要与爬取结果原样进参数，回填顺序一致。"""
    jobs = [_job(f"j{i}") for i in range(15)]
    provider, calls = _provider(monkeypatch, [_sheet(jobs)])
    out = await provider.score_batch(RESUME, jobs)
    assert len(calls) == 1
    assert json.loads(calls[0]["jobs_json"]) == jobs
    assert json.loads(calls[0]["resume_json"]) == RESUME
    assert [r.score for r in out] == [0.8] * 15


async def test_retry_feeds_first_error(monkeypatch):
    """第二次调用的提示词尾部带第一次的解析错误。"""
    templates = []
    replies = [{"scores": [{"job_id": "j1", "score": "high"}]}, _sheet([_job("j1")], 0.7)]

    async def fake(template_text, inputs):
        templates.append(template_text)
        return replies.pop(0)

    monkeypatch.setattr(llm_mod, "ainvoke_json", fake)
    out = await LlmMatchProvider(TEMPLATE).score_batch(RESUME, [_job("j1")])
    assert out[0].score == 0.7
    assert templates[0] == TEMPLATE
    assert "high" in templates[1]                      # float('high') 的报错被喂回
    assert "解析失败" in templates[1]


async def test_parse_failure_retries_once(monkeypatch):
    """分数不是数 = 解析失败：重试一次，第二次通过。"""
    bad = {"scores": [{"job_id": "j1", "score": "high"}]}
    provider, calls = _provider(monkeypatch, [bad, _sheet([_job("j1")], 0.7)])
    out = await provider.score_batch(RESUME, [_job("j1")])
    assert len(calls) == 2
    assert out[0].score == 0.7


async def test_double_failure_leaves_score_empty(monkeypatch):
    """两次都解析失败：分数留空，不造中性分。"""
    bad = {"nope": 1}
    provider, calls = _provider(monkeypatch, [bad, bad])
    out = await provider.score_batch(RESUME, [_job("j1")])
    assert len(calls) == 2
    assert out[0].score is None


async def test_missing_job_score_empty(monkeypatch):
    """模型漏返某岗：该岗分数留空，其余岗不受影响。"""
    provider, _ = _provider(monkeypatch, [_sheet([_job("j2")])])
    out = await provider.score_batch(RESUME, [_job("j1"), _job("j2")])
    assert out[0].score is None and out[1].score == 0.8


async def test_service_drops_empty_scores(monkeypatch):
    """空分不入列；全空时由 assert_non_empty 走无匹配岗位收口。"""
    provider, _ = _provider(monkeypatch, [{"nope": 1}, {"nope": 1}])
    ranked, below_min = await MatchService(provider).match(RESUME, [_job("j1")], set())
    assert ranked == [] and below_min is False
    with pytest.raises(AppError) as e:
        MatchService.assert_non_empty(ranked)
    assert e.value.error is ErrorCode.NO_MATCHED_JOB


async def test_service_caps_pool_before_scoring(monkeypatch):
    """20 个入参：先剔已打招呼再截到 15，一次调用只喂 15 个。"""
    jobs = [_job(f"j{i}") for i in range(20)]
    provider, calls = _provider(monkeypatch, [_sheet(jobs[:MAX_CANDIDATES], 0.5)])
    ranked, below_min = await MatchService(provider).match(RESUME, jobs, set())
    assert len(json.loads(calls[0]["jobs_json"])) == MAX_CANDIDATES
    assert len(ranked) == MAX_CANDIDATES and below_min is False


async def test_service_sorts_desc_and_excludes_greeted(monkeypatch):
    jobs = [_job("j1"), _job("j2"), _job("j3")]
    sheet = {"scores": [{"job_id": "j2", "score": 0.9}, {"job_id": "j3", "score": 0.4}]}
    provider, calls = _provider(monkeypatch, [sheet])
    ranked, _ = await MatchService(provider).match(RESUME, jobs, {"j1"})
    assert [r.job["job_id"] for r in ranked] == ["j2", "j3"]
    assert len(json.loads(calls[0]["jobs_json"])) == 2   # 已打招呼的不占打分 token