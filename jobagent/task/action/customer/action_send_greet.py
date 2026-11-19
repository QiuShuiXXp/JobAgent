"""action_send_greet：勾选即开工——建任务与逐岗位明细，起后台协程，回一句"已开始"。

三道门禁（风控/配额那些都删了，剩下的都是"根本不该开工"的情形）：
登录态 → 勾选有效性 → 已打招呼去重。真正的逐岗位沟通在 TaskManager 后台协程里，
页面操作在 GreetExecutor（打法对齐 boss-skill-main auto_apply_jobs）。
"""
import uuid

from jobagent.api.errors import AppError, ErrorCode
from jobagent.config.settings import settings
from jobagent.domain.messages import BotMessage, FocusedObject, MessageType
from jobagent.domain.state import DialogueState
from jobagent.infrastructure.db import get_session
from jobagent.model.business_models import GreetTaskItemRecord, GreetTaskRecord
from jobagent.repository.business_repositories import CandidateJobRepository
from jobagent.services.task_manager import TaskManager
from jobagent.task.action.base import Action, ActionResult

DRY_HINT = ("当前是演练模式（GREET_DRY_RUN=true）：只会打开详情页确认沟通入口，"
            "不会真的给 BOSS 发消息。")


class ActionSendGreet(Action):
    name = "action_send_greet"

    def __init__(self, task_manager: TaskManager) -> None:
        self.task_manager = task_manager

    async def run(self, state: DialogueState, action_args: dict) -> ActionResult:
        user_id = state.sender_id
        self.require_login(state)

        selected = [str(j) for j in (state.get_slots("job_selection") or []) if str(j).strip()]
        selected = list(dict.fromkeys(selected))
        if not selected:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "尚未勾选任何岗位")

        async with get_session() as session:
            repo = CandidateJobRepository(session)
            greeted = await repo.greeted_job_ids(user_id)
            dup = [j for j in selected if j in greeted]
            if dup:
                raise AppError(ErrorCode.JOB_ALREADY_GREETED,
                               f"这些岗位已经打过招呼了: {dup}", http_status=409)
            known = await repo.find_by_ids(user_id, selected)
            missing = [j for j in selected if j not in known]
            if missing:
                raise AppError(ErrorCode.INVALID_ARGUMENT,
                               f"勾选的岗位不在候选列表，请重新筛岗: {missing}")

            task_id = str(uuid.uuid4())
            session.add(GreetTaskRecord(task_id=task_id, user_id=user_id,
                                        status="PENDING", total_count=len(selected)))
            for idx, job_id in enumerate(selected):
                session.add(GreetTaskItemRecord(id=str(uuid.uuid4()), task_id=task_id,
                                                job_id=job_id, seq=idx,
                                                item_status="PENDING"))
            await session.commit()

        self.task_manager.submit(task_id)

        card = BotMessage(type=MessageType.OBJECT, object=FocusedObject(
            type="task_report", id=task_id,
            payload={"phase": "created", "total": len(selected),
                     "dry_run": settings.greet_dry_run}))
        text = f"已开始逐岗位沟通，共 {len(selected)} 个，跑完我把结果发给你。"
        if settings.greet_dry_run:
            text += DRY_HINT
        return ActionResult(messages=[card, BotMessage(text=text)])
