"""
LLM 打分：爬取结果直喂模型，一次调用批量打分。
重试时把第一次的解析错误喂回模型；两次都失败分数留空，不造分。
"""
import json
import logging

from jobagent.infrastructure.llm_client import ainvoke_json
from jobagent.matching.base import MatchResult

logger = logging.getLogger("jobagent.match")

# 重试时附在提示词尾部：让模型对着第一次的错误修
REPAIR_SUFFIX = """
上次输出解析失败，错误：{errors}
请严格按输出结构重新输出完整 JSON，覆盖清单中全部岗位。"""


class LlmMatchProvider:
    name = "llm"

    def __init__(self, template_text: str) -> None:
        self.template_text = template_text

    async def score_batch(self, resume: dict, jobs: list[dict]) -> list[MatchResult]:
        # 1. 参数：简历摘要（上传时已提取存槽）与爬取结果原样进提示词
        inputs = {
                  "resume_json": json.dumps(resume, ensure_ascii=False),
                  "jobs_json": json.dumps(jobs, ensure_ascii=False)
                  }

        # 2. 调模型输出 JSON 并解析；失败把错误喂回重试一次，再失败评分表留空
        sheet: dict[str, tuple[float, list[str]]] = {}
        repair = ""  # 第二次失败时追加错误信息
        for attempt in (1, 2):
            try:
                data = await ainvoke_json(self.template_text + repair, inputs)
                sheet = {str(s["job_id"]): (float(s["score"]), list(s.get("reasons") or []))
                         for s in data["scores"]}
                break
            except Exception as e:
                logger.warning("打分 JSON 解析失败（第 %d 次）：%r", attempt, e)
                repair = REPAIR_SUFFIX.format(errors=repr(e))

        # 3. 按入参顺序回填：有分给分，没分留空
        results: list[MatchResult] = []
        for job in jobs:
            hit = sheet.get(job["job_id"])
            results.append(MatchResult(score=hit[0], reasons=hit[1]) if hit
                           else MatchResult(score=None))
        return results