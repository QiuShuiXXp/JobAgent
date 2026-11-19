"""sessions：情景记忆的折叠状态（滚动折叠第一期表）。"""
from datetime import datetime, timezone

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from jobagent.model.base import Base, Timestamp


class SessionRecord(Base):
    """一行 = 一个会话的折叠断点 + 摘要。

    只有折过摘要的会话才有行——短会话不值得折，就不占这一行。
    将来做历史列表（会话档案）时再按需求加 started_at/closed_at 等列，与本表不冲突。
    """
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_sender_updated", "sender_id", "updated_at"),)

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sender_id: Mapped[str] = mapped_column(String(64), nullable=False)
    folded_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))
