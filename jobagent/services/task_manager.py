
import asyncio
import logging
import random
from datetime import datetime, timezone

from sqlalchemy import select, update

from jobagent.config.settings import settings
from jobagent.infrastructure.browser import BrowserManager
from jobagent.infrastructure.db import get_session
from jobagent.infrastructure.log_config import bind_task, bind_user
from jobagent.model.business_models import (
    CandidateJobRecord, GreetTaskItemRecord, GreetTaskRecord,
)
from jobagent.prompts.loader import PromptLoader
from jobagent.services.greet_executor import GreetExecutor

"""
TaskManager：勾选即起后台任务，逐岗位串行沟通，逐条如实记录结果。

- 不重试、不补偿、不续跑——没沟通成功的就是一条失败记录，跑完汇总告诉用户；
- 风控只留最钝的两招：岗位之间随机等 60~180 秒；一旦命中平台安全校验或掉登录，
  剩余岗位不再尝试，直接记失败原因。

权威数据在 数据库（greet_tasks / greet_task_items），进程内字典只是执行协程句柄。
"""
logger = logging.getLogger("jobagent.task_manager")

# 任务终态；明细只有 PENDING/SUCCEEDED/DRY_RUN/FAILED 四种
TERMINAL = ("SUCCESS", "FAILED", "PARTIAL_SUCCESS")
THROTTLE_S = (60.0, 180.0)     # 真实发送时的岗位间隔
DRY_THROTTLE_S = (1.0, 2.0)    # 演练不等人，否则自测没法看
# 命中这两类结果就不再碰剩下的岗位：继续戳只会让平台把账号扣得更死
STOP_STATUSES = ("RATE_LIMITED", "LOGIN_INVALID")
STOP_REASONS = {
    "RATE_LIMITED": "已停止：平台触发安全校验，剩余岗位未执行",
    "LOGIN_INVALID": "已停止：登录态失效，剩余岗位未执行",
}


class TaskManager:
    def __init__(self, greet_executor: GreetExecutor, browser: BrowserManager) -> None:
        self.greet_executor = greet_executor
        self.browser = browser
        self.prompts = PromptLoader()
        self._active: dict[str, asyncio.Task] = {}

    def submit(self, task_id: str) -> None:
        """起执行协程；字典查重防同一任务被点两次。"""
        if task_id in self._active:
            return
        self._active[task_id] = asyncio.create_task(self._run_task(task_id))

    async def shutdown(self) -> None:
        for handle in list(self._active.values()):
            handle.cancel()
        if self._active:
            await asyncio.gather(*self._active.values(), return_exceptions=True)
        self._active.clear()

    async def result(self, task_id: str) -> dict | None:
        """任务结果（/api/tasks 轮询 + 汇总话术的唯一数据源）。"""
        async with get_session() as session:
            task = (await session.execute(select(GreetTaskRecord)
                    .where(GreetTaskRecord.task_id == task_id))).scalar_one_or_none()
            if task is None:
                return None
            items = (await session.execute(select(GreetTaskItemRecord)
                .where(GreetTaskItemRecord.task_id == task_id)
                .order_by(GreetTaskItemRecord.seq))).scalars().all()
            titles = dict((await session.execute(
                select(CandidateJobRecord.job_id, CandidateJobRecord.title)
                .where(CandidateJobRecord.user_id == task.user_id))).all())
        ok = sum(1 for i in items if i.item_status in ("SUCCEEDED", "DRY_RUN"))
        return {
            "task_id": task_id,
            "status": task.status,
            "finished": task.status in TERMINAL,
            "dry_run": settings.greet_dry_run,
            "total": len(items),
            "succeeded": ok,
            "failed": len(items) - ok,
            "items": [{"job_id": i.job_id, "title": titles.get(i.job_id, ""),
                       "item_status": i.item_status,
                       "failure_reason": i.failure_reason} for i in items],
        }

    # ================= 执行协程 =================
    async def _run_task(self, task_id: str) -> None:
        user_id = await self._task_user(task_id)
        if user_id is None:
            return
        bind_task(task_id)
        bind_user(user_id)
        try:
            await self._mark(task_id, status="RUNNING")
            page = await self.browser.get_context(user_id)
            try:
                # 必须先 get_context：cookie 只能从已启动的实例读，冷启动直接探针必 False
                if not await self.browser.logged_in(user_id):
                    await self._fail_rest(task_id, "登录态已失效，未执行任何岗位")
                    await self._mark(task_id, status="FAILED")
                    return
                for item in await self._pending_items(task_id, user_id):
                    outcome = await self.greet_executor.greet_one(
                        page, {"job_id": item["job_id"], "title": item["title"]},
                        self._render_copy(item["title"], item["company"]))
                    await self._record_item(task_id, user_id, item["job_id"], outcome)
                    if outcome.status in STOP_STATUSES:
                        await self._fail_rest(task_id, STOP_REASONS[outcome.status])
                        break
                    await asyncio.sleep(random.uniform(
                        *(DRY_THROTTLE_S if settings.greet_dry_run else THROTTLE_S)))
            finally:
                # 任务结束收掉实例，下一轮重新起
                await self.browser.release_context(user_id)
            await self._finalize(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("任务 %s 异常退出", task_id)
            await self._fail_rest(task_id, "已停止：执行异常")
            await self._mark(task_id, status="FAILED")
        finally:
            self._active.pop(task_id, None)

    def _render_copy(self, title: str, company: str) -> str:
        return self.prompts.render("greet_copy.j2", position=title, company=company)

    # ================= DB 辅助 =================
    async def _task_user(self, task_id: str) -> str | None:
        async with get_session() as session:
            return (await session.execute(select(GreetTaskRecord.user_id)
                .where(GreetTaskRecord.task_id == task_id))).scalar_one_or_none()

    async def _pending_items(self, task_id: str, user_id: str) -> list[dict]:
        """PENDING 明细 + 岗位标题/公司，按 seq 走——就是用户勾选的顺序。"""
        async with get_session() as session:
            rows = (await session.execute(
                select(GreetTaskItemRecord.job_id)
                .where(GreetTaskItemRecord.task_id == task_id,
                       GreetTaskItemRecord.item_status == "PENDING")
                .order_by(GreetTaskItemRecord.seq))).scalars().all()
            jobs = (await session.execute(select(CandidateJobRecord.job_id,
                CandidateJobRecord.title, CandidateJobRecord.company)
                .where(CandidateJobRecord.user_id == user_id,
                       CandidateJobRecord.job_id.in_(list(rows))))).all() if rows else []
        info = {j: {"title": t or j, "company": c or ""} for j, t, c in jobs}
        return [{"job_id": j, **info.get(j, {"title": j, "company": ""})} for j in rows]

    async def _record_item(self, task_id: str, user_id: str, job_id: str,
                           outcome) -> None:
        status = outcome.status if outcome.status in ("SUCCEEDED", "DRY_RUN") else "FAILED"
        reason = "" if status == "SUCCEEDED" else (outcome.failure_reason or "沟通未成功")
        async with get_session() as session:
            await session.execute(update(GreetTaskItemRecord)
                .where(GreetTaskItemRecord.task_id == task_id,
                       GreetTaskItemRecord.job_id == job_id,
                       GreetTaskItemRecord.item_status == "PENDING")
                .values(item_status=status, failure_reason=reason[:500],
                        executed_at=_utcnow()))
            if status == "SUCCEEDED":
                # 已打招呼去重源：下轮检索/再建任务都排除它（演练不算，没真发过）
                await session.execute(update(CandidateJobRecord)
                    .where(CandidateJobRecord.user_id == user_id,
                           CandidateJobRecord.job_id == job_id)
                    .values(status="GREETED"))
            await session.commit()
        logger.info("任务 %s 岗位 %s -> %s %s", task_id, job_id, status, reason)

    async def _fail_rest(self, task_id: str, reason: str) -> None:
        """剩下的岗位一律记失败并写明原因——不重试、不排队等恢复。"""
        async with get_session() as session:
            await session.execute(update(GreetTaskItemRecord)
                .where(GreetTaskItemRecord.task_id == task_id,
                       GreetTaskItemRecord.item_status == "PENDING")
                .values(item_status="FAILED", failure_reason=reason[:500],
                        executed_at=_utcnow()))
            await session.commit()

    async def _finalize(self, task_id: str) -> None:
        async with get_session() as session:
            statuses = (await session.execute(select(GreetTaskItemRecord.item_status)
                .where(GreetTaskItemRecord.task_id == task_id))).scalars().all()
        ok = sum(1 for s in statuses if s in ("SUCCEEDED", "DRY_RUN"))
        status = ("SUCCESS" if ok == len(statuses)
                  else "FAILED" if ok == 0 else "PARTIAL_SUCCESS")
        await self._mark(task_id, status=status)
        logger.info("任务 %s 结束：%s（%d/%d）", task_id, status, ok, len(statuses))

    async def _mark(self, task_id: str, **values) -> None:
        async with get_session() as session:
            await session.execute(update(GreetTaskRecord)
                .where(GreetTaskRecord.task_id == task_id).values(**values))
            await session.commit()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
