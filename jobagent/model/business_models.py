"""业务表 ORM：候选岗位 + 打招呼任务（会话/状态表见 state_record、session_record）。"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, CHAR, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from jobagent.model.base import Base, Timestamp


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CandidateJobRecord(Base):
    """candidate_jobs：候选岗位；status=GREETED 承担已打招呼去重来源。"""
    __tablename__ = "candidate_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_candidate_jobs_user_job"),
        Index("ix_candidate_jobs_user_score", "user_id", "match_score"),
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)      # 平台侧唯一标识
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    salary_text: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)      # CANDIDATE/SELECTED/GREETED/FAILED/FILTERED/TERMINATED
    created_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False, default=_now)


class GreetTaskRecord(Base):
    """greet_tasks：打招呼任务批次；结果明细在 greet_task_items，一次跑完不续跑。"""
    __tablename__ = "greet_tasks"
    __table_args__ = (
        Index("ix_greet_tasks_user_created", "user_id", "created_at"),
    )

    task_id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)      # PENDING/RUNNING/SUCCESS/PARTIAL_SUCCESS/FAILED
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(Timestamp, nullable=False, default=_now)


class GreetTaskItemRecord(Base):
    """greet_task_items：逐岗位执行结果；唯一约束兜底防重放。"""
    __tablename__ = "greet_task_items"
    __table_args__ = (
        UniqueConstraint("task_id", "job_id", name="uq_greet_task_items_task_job"),
        Index("ix_greet_task_items_task_status", "task_id", "item_status"),
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # 勾选顺序，也是执行与汇报顺序
    item_status: Mapped[str] = mapped_column(String(16), nullable=False)  # PENDING/SUCCEEDED/DRY_RUN/FAILED
    failure_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    executed_at: Mapped[datetime | None] = mapped_column(Timestamp, nullable=True)