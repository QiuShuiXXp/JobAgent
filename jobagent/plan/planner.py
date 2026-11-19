import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from jobagent.domain.contexts import CollectedSystemContext
from jobagent.domain.messages import UserMessage
from jobagent.domain.state import DialogueState
from jobagent.history.builder import ChatHistoryBuilder
from jobagent.history.window import HistoryWindow
from jobagent.infrastructure.llm_client import ainvoke_json
from jobagent.knowledge.intents import KNOWLEDGE_INTENTS
from jobagent.plan.turn_plan import TurnPlan, TaskTurnPlan, KnowledgeTurnPlan
from jobagent.task.flow.flows import FlowsList
from jobagent.task.flow.gates import FlowGate

# 情景记忆入口：(sender_id) -> 历史折叠摘要；实现方负责失败降级为空串
SummaryLookup = Callable[[str], Awaitable[str]]


class TurnPlanner:
    """LLM 规划器：把用户消息 + 当前状态 → 本轮要执行的流程命令（或知识意图）。

    主链路顺序由注入的 FlowGate（DAG 门禁）控制：前置流程未完成 → 流程不解锁，
    不进 LLM 可选清单；未解锁流程连同其前置一起喂给 LLM，把超前的诉求引到主链路上。
    """

    def __init__(
        self, template_text: str,
        summary_lookup: SummaryLookup | None = None,
        window: HistoryWindow | None = None,
        flow_gate: FlowGate | None = None
    ) -> None:
        self.template_text = template_text
        self.summary_lookup = summary_lookup
        self.window = window or HistoryWindow.from_settings()
        self.flow_gate = flow_gate or FlowGate()

    async def predict(self, state: DialogueState, user_message: UserMessage, flow_list: FlowsList) -> TurnPlan:
        # 构建llm提示词
        prompt = await self._build_llm_prompt(user_message=user_message, state=state, flow_list=flow_list)

        # 调用llm生成回复,并解析为TurnPlan对象
        return await self._parse_to_turnPlan(prompt)

    async def _build_llm_prompt(self, user_message: UserMessage, state, flow_list: FlowsList):
        user_text = user_message.text if user_message else ""
        # 已结束的业务流程（DAG 门禁输入：前置流程全部完成才解锁）
        completed = state.completed_flows
        # 可选择的业务流程列表（只含已解锁的）
        available_flows_json = json.dumps(
            [{"flow_id": f.flow_id, "name": f.flow_name, "description": f.description}
             for f in self.flow_gate.unlocked_flows(flow_list, completed)],
            ensure_ascii=False)
        # 当前对话中被中断的任务列表
        interrupted_tasks_json = json.dumps([t.flow_id for t in state.interrupted_active_tasks], ensure_ascii=False)
        # 当前对话中正在执行的任务列表
        sys_ctx = state.active_system_task
        pending_collect = None
        if isinstance(sys_ctx, CollectedSystemContext):
            pending_collect = {"slot_name": sys_ctx.slot_name,
                               "question": (sys_ctx.response or {}).get("text", "")}
        # 只报业务流程：active_task 挂着系统过场上下文时不能把 system_task_* 喂给规划器
        biz = state.active_task
        active_task_json = json.dumps(
            {"flow_id": biz.flow_id,
             "slots": {k: str(v) for k, v in biz.slots.items()},
             "pending_collect": pending_collect,
             } if biz else None, ensure_ascii=False)
        # 历史对话文本：窗口裁剪（轮数 + 字符预算）——够解「那家怎么样」这类指代就行。
        session = state.current_session()
        history_text = ChatHistoryBuilder.build(self.window.select(session.turns)) if session else ""
        # 已收集的求职意向（全局记忆视图）：改意向时规划器要知道哪些槽有旧值，
        slots_view = state.slots_view
        intent_slots_json = json.dumps(
            {k: slots_view[k] for k in ("intent_position", "intent_cities", "intent_salary")
             if slots_view.get(k) is not None}, ensure_ascii=False)
        # 当前对话中涉及的实体列表
        focused_object_json = json.dumps(
            state.focused_object.to_dict() if state.focused_object else None, ensure_ascii=False
        )
        # 未解锁流程及其前置流程：提示词第 2 条靠它把「还没到那一步」的诉求接到主链路上
        locked_flows_json = json.dumps(self.flow_gate.locked_flows(flow_list, completed), ensure_ascii=False)
        # 知识意图清单
        knowledge_intents_json = json.dumps(
            [{"intent": k, **{f: v for f, v in asdict(i).items() if f != "provider_ids"}}
             for k, i in KNOWLEDGE_INTENTS.items()], ensure_ascii=False)
        # 历史折叠摘要（情景记忆）：上个会话的整场，或当前会话的前半场
        history_summary_text = await self._history_summary(state)
        return {
            "user_text": user_text,
            "available_flows_json": available_flows_json,
            "locked_flows_json": locked_flows_json,
            "interrupted_tasks_json": interrupted_tasks_json,
            "active_task_json": active_task_json,
            "history_text": history_text,
            "intent_slots_json": intent_slots_json,
            "focused_object_json": focused_object_json,
            "knowledge_intents_json": knowledge_intents_json,
            "history_summary": history_summary_text
        }

    async def _history_summary(self, state: DialogueState) -> str:
        """历史折叠摘要（情景记忆）。没接 lookup 就返回空串——模板里显示「无」。"""
        if self.summary_lookup is None:
            return ""
        return await self.summary_lookup(state.sender_id)

    async def _parse_to_turnPlan(self, prompt) -> TurnPlan:
        llm_response = await ainvoke_json(template_text=self.template_text, inputs=prompt)

        plan = TurnPlan()
        if "task" in llm_response:
            plan.task = TaskTurnPlan(commands=llm_response["task"].get("commands", []))
        if "knowledge" in llm_response:
            plan.knowledge = KnowledgeTurnPlan(
                intents=llm_response["knowledge"].get("intents", []))
        return plan
