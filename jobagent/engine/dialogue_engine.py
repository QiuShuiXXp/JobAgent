import logging
import time

from jobagent.engine.clarify import ClarifyResponser
from jobagent.domain.contexts import CollectedSystemContext
from jobagent.domain.messages import UserMessage, MessageType, BotMessage, ProcessResult
from jobagent.domain.state import DialogueState
from jobagent.knowledge.handler import KnowledgeHandler
from jobagent.knowledge.providers.base import KnowledgeQuery
from jobagent.plan.planner import TurnPlanner
from jobagent.task.flow.gates import FlowGate
from jobagent.plan.turn_plan import ClarifyReason, KnowledgeTurnPlan
from jobagent.plan.validator import TurnPlanValidator
from jobagent.task.command.commands import Command, SetSlotsCommand, StartedFlowCommand
from jobagent.task.handler import TaskHandler

logger = logging.getLogger("jobagent.engine")


class DialogueEngine:
    """DialogueEngine：一轮对话的顶层调度（TEXT 主链路；OBJECT 分派）。"""
    def __init__(self,
                 task_handler: TaskHandler,
                 planner: TurnPlanner,
                 clarify: ClarifyResponser,
                 validator:TurnPlanValidator,
                 knowledge_handler:KnowledgeHandler,
                 session_timeout_minutes: int = 60,
                 on_session_closed=None,
                 on_turn_committed=None,
                 flow_gate: FlowGate = None,
                 ):
        self.session_timeout_seconds = session_timeout_minutes * 60
        self.planner=planner
        self.task_handler=task_handler
        self.validator=validator
        self.knowledge_handler=knowledge_handler
        self.clarify=clarify
        # 会话过期回调：登录记录属于上一个会话，翻篇时退出登录
        self.on_session_closed=on_session_closed
        # 每轮提交后的钩子：滚动折叠在这里触发（同步调用，只派后台任务不等待）
        self.on_turn_committed=on_turn_committed
        self.flow_gate = flow_gate or FlowGate()

    async def handle_message(self, user_message: UserMessage, state: DialogueState):

        # 开启Session
        await self._prepare_session(state)
        # 开启本轮Turn
        state.begin_turn(user_message=user_message)

        # 3 分派
        # 3.1消息类型为对象类型
        if user_message.type == MessageType.OBJECT:
            messages = await self._hand_object(user_message, state)
        else:
        # 3.2消息类型为文本
            messages = await self._hand_text(user_message, state)

        # 提交本轮turn
        state.pending_turn.bot_messages.extend(messages)
        # 响应以 turn 落盘那份为准：命令处理器会直接往 turn 里追加卡片（取消登录翻篇），
        # 只回 executor 的 messages 会漏掉它们，前端永远收不到
        messages = state.pending_turn.bot_messages
        state.commit_pending_turn()
        if self.on_turn_committed is not None:
            self.on_turn_committed(state)

        return ProcessResult(sender_id=state.sender_id,
                             message_id=user_message.message_id, messages=messages)

    async def _prepare_session(self, state):
        session=state.current_session()
        now = time.time()
        if session is None:
            # 开启一个session
            state.start_session()
            return
        else:
            # 判断session是否过期
            if now - session.last_activity_at > self.session_timeout_seconds:
                state.close_current_session()
                # 清理Session中临时的资源
                if self.on_session_closed is not None:
                    await self.on_session_closed(state)
                state.reset_runtime_state_for_new_session()
                state.start_session()



    async def _hand_object(self, user_message, state):
        """
        卡片类型: 1、resume 2、job_list 3、login_window
        手动赋值commands
        """
        obj = user_message.object
        state.set_focused_object(obj)
        if obj.type == "resume":  # 简历上传
            commands = [
                StartedFlowCommand(flow_id="resume_upload_confirm"),
                # null = 清掉上一份简历的确认标记：新简历必须重新过确认问句，
                # 否则旧槽位播种进新流程，collect 见值直接放行，问句和按钮都被跳过
                SetSlotsCommand(slots={"resume_file_ref": obj.id, "resume_confirmed": None}),
            ]
            return await self.task_handler.handle(commands,state=state)

        if obj.type == "job_list":  # 招聘列表
            commands = [SetSlotsCommand(slots={"job_selection": obj.payload.get("job_selection", [])})]
            return await self.task_handler.handle(commands, state)

        if obj.type == "login_window":  # 登录窗口
            # 前端轮询检测到登录态后只带 login_confirmed 这一个值，交给 collect 节点预填；
            # login_status 一律由后端读 cookie 判定，别让前端回传冲掉它
            return await self.task_handler.handle([], state)

        return [BotMessage(text="已收到你的操作。")]


    async def _hand_text(self, user_message, state):
        #  主链路：规划 → 校验 → 两轨道
        session = state.current_session()
        if session is not None:
            session.last_activity_at = time.time()
        # 规划
        plan=await self.planner.predict(state=state,user_message=user_message,flow_list=self.task_handler.flow_list)
        # 校验
        validate_plan=self.validator.validate(turn_plan=plan,state=state)
        # 规划落空/重问这类偶发问题只能靠这行定位：终端直接看到本轮命令与校验结论
        logger.info("规划：%s｜校验：%s",
                    plan.task.commands if plan.task else
                    (plan.knowledge.intents if plan.knowledge else "{}"),
                    validate_plan.reason if not validate_plan.valid else "ok")
        if not validate_plan.valid:
            # 门禁挡住：直接把前置流程开起来
            if validate_plan.reason == ClarifyReason.FLOW_LOCKED:
                # 获取要开启的流程id
                target = self.flow_gate.unlock_target(state.completed_flows, validate_plan.flow_id)
                active = state.active_task
                # 前置就是当前活跃流程时再开一次是 no-op：落到下面的澄清，说清哪件事被卡住
                if target is not None and (active is None or active.flow_id != target):
                    return await self.task_handler.handle(
                        [StartedFlowCommand(flow_id=target)], state)
            # 收集进行中时规划落空不该甩菜单：重问当前问句，流程才接得下去
            # （弱模型对「继续/否定回答」常输出 {}，甩菜单会把正在收集的流程拦腰切断）
            if validate_plan.reason != ClarifyReason.FLOW_LOCKED:
                question = self._pending_question(state)
                if question is not None:
                    return [question]
            # 3.4a 校验失败 → 分类澄清（不改状态）；门禁这条要说清是哪件事被卡住
            blocked = self.task_handler.flow_list.get_flow_by_id(validate_plan.flow_id or "")
            return [await self.clarify.respond(validate_plan.reason,
                                               blocked.flow_name if blocked else "")]
        # 3.4b 两轨道分派（空规划已在 validator 走 MISSING_TRACK 澄清，不会到此）

        # 业务轨道处理
        if plan.task is not None:
            commands=[Command.from_dict(command) for command in plan.task.commands]
            return await self.task_handler.handle(commands,state=state)

        # 知识库轨道：规划器只给 intent + objects，
        if plan.knowledge is not None:
            queries = [KnowledgeQuery.from_dict(intent) for intent in plan.knowledge.intents]
            return await self.knowledge_handler.handle(state=state, queries=queries)

    @staticmethod
    def _pending_question(state: DialogueState) -> BotMessage | None:
        """正在等回答的 collect 问句；没在收集返回 None。"""
        ctx = state.active_system_task
        if isinstance(ctx, CollectedSystemContext):
            text = (ctx.response or {}).get("text")
            if text:
                return BotMessage(text=text)
        return None



