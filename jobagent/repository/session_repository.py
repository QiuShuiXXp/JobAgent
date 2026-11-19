"""sessions 表：折叠断点与摘要的读写（纯 CRUD，用例在 history/summarizer.py）。

取行再写，避开 MySQL 专有的 on_duplicate_key_update——桌面端是 SQLite，用不了。
"""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jobagent.model.session_record import SessionRecord


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fold_state(self, session_id: str) -> tuple[int, str]:
        """(已折叠轮数, 现有摘要)；没有行返回 (0, "")。"""
        row = (await self.session.execute(
            select(SessionRecord.folded_turns, SessionRecord.summary)
            .where(SessionRecord.session_id == session_id))).one_or_none()
        return (row[0], row[1] or "") if row else (0, "")

    async def latest_summary(self, sender_id: str) -> str:
        """最近折叠的一条摘要（可能是当前会话的半场）；没有返回空串。"""
        summary = (await self.session.execute(
            select(SessionRecord.summary)
            .where(SessionRecord.sender_id == sender_id,
                   SessionRecord.summary != "")
            .order_by(SessionRecord.updated_at.desc())
            .limit(1))).scalars().first()
        return summary or ""

    async def save_fold(self, session_id: str, sender_id: str,
                        summary: str, folded_turns: int) -> None:
        """摘要与断点一起写（upsert）；断点只在成功后前移。"""
        row = await self.session.get(SessionRecord, session_id)
        if row is None:
            self.session.add(SessionRecord(session_id=session_id, sender_id=sender_id,
                                           summary=summary, folded_turns=folded_turns))
        else:
            row.summary = summary
            row.folded_turns = folded_turns
        await self.session.commit()

    async def delete_by_sender(self, sender_id: str) -> int:
        """清空该用户所有折叠摘要（清空历史时调用）。"""
        result = await self.session.execute(
            delete(SessionRecord).where(SessionRecord.sender_id == sender_id))
        await self.session.commit()
        return result.rowcount
