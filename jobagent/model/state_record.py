"""dialogue_states：对话状态 JSON document"""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from jobagent.model.base import Base


class DialogueStateRecord(Base):
    __tablename__ = "dialogue_states"

    sender_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )