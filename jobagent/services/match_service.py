
"""匹配流水线：去重过滤 → 截断到每轮上限 → Provider 批量打分 → 空分剔除 → 降序。"""
from dataclasses import dataclass

from jobagent.api.errors import AppError, ErrorCode
from jobagent.matching.base import MatchResult


@dataclass(slots=True)
class RankedJob:
    job: dict
    result: MatchResult


# 候选列表数量：每轮上限（LLM 按一次调用批量打分，截断控制单轮 token 成本），
# 低于下限提示用户放宽条件
MAX_CANDIDATES = 15
MIN_CANDIDATES = 5


class MatchService:
    def __init__(self, provider) -> None:
        self.provider = provider

    async def match(self, resume: dict, jobs: list[dict],
                    greeted_job_ids: set[str]) -> tuple[list[RankedJob], bool]:
        """返回 (排序后列表, 是否低于最低数量提示)。"""
        ranked: list[RankedJob] = []
        # 已打招呼的岗位先剔掉：既不入列，也不该占打分的 token
        pool = [job for job in jobs if job["job_id"] not in greeted_job_ids]
        # 先截断再打分：保证一次 LLM 调用最多覆盖 MAX_CANDIDATES 个岗位
        pool = pool[:MAX_CANDIDATES]
        results = await self.provider.score_batch(resume, pool)
        for job, r in zip(pool, results):
            # 分数为空不入列：模型解析失败不造分，宁缺毋滥
            if r.score is None:
                continue
            ranked.append(RankedJob(job=job, result=r))

        ranked.sort(key=lambda x: x.result.score, reverse=True)
        return ranked, (0 < len(ranked) < MIN_CANDIDATES)

    @staticmethod
    def assert_non_empty(ranked: list[RankedJob]) -> None:
        if not ranked:
            raise AppError(ErrorCode.NO_MATCHED_JOB, "无匹配岗位，建议放宽筛选条件")