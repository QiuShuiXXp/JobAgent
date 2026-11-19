
"""业务数据仓储：候选岗位。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jobagent.model.business_models import CandidateJobRecord


class CandidateJobRepository:
    """candidate_jobs：候选岗落库；status=GREETED 是已打招呼去重源（spec 5.4.1-4）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def greeted_job_ids(self, user_id: str) -> set[str]:
        rows = (await self.session.execute(select(CandidateJobRecord.job_id)
            .where(CandidateJobRecord.user_id == user_id,
                   CandidateJobRecord.status == "GREETED"))).scalars().all()
        return set(rows)

    async def known_job_ids(self, user_id: str) -> set[str]:
        """这个用户见过的全部岗位（候选表即历史展示记录，「换一批」靠它排除）。"""
        rows = (await self.session.execute(select(CandidateJobRecord.job_id)
            .where(CandidateJobRecord.user_id == user_id))).scalars().all()
        return set(rows)

    async def find_by_ids(self, user_id: str, job_ids: list[str]) -> dict[str, dict]:
        """勾选岗位 → 候选表行。"""
        if not job_ids:
            return {}
        rows = (await self.session.execute(select(CandidateJobRecord)
            .where(CandidateJobRecord.user_id == user_id,
                   CandidateJobRecord.job_id.in_(job_ids)))).scalars().all()
        return {r.job_id: {"job_id": r.job_id, "title": r.title, "company": r.company,
                           "city": r.city, "salary_text": r.salary_text} for r in rows}

    async def upsert_candidates(self, user_id: str, candidates: list[dict]) -> int:
        """candidates 元素键 = 表列（job_id/title/company/salary_text/city/match_score/score_reasons）。

        GREETED/SELECTED 是终态：刷新分数但不把状态打回 CANDIDATE。
        """
        for c in candidates:
            row = (await self.session.execute(select(CandidateJobRecord)
                .where(CandidateJobRecord.user_id == user_id,
                       CandidateJobRecord.job_id == c["job_id"]))).scalar_one_or_none()
            if row is None:
                self.session.add(CandidateJobRecord(
                    user_id=user_id, status="CANDIDATE", **c))
            else:
                for key, value in c.items():
                    setattr(row, key, value)
                if row.status not in ("GREETED", "SELECTED"):
                    row.status = "CANDIDATE"
        await self.session.commit()
        return len(candidates)