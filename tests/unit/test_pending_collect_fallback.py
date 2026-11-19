"""回归：collect 进行中规划落空要重问而不是甩菜单；门禁前置=活跃流程时要给引导。

背景（2026-11-18 实测）：弱模型对「继续/否定回答」输出 {} → MISSING_TRACK → 通用菜单，
正在收集的意向流程被拦腰切断；boss_login 的 depends_on 丢失后门禁引导整条失效。
"""
from dataclasses import asdict

from jobagent.domain.contexts import CollectedSystemContext, TaskContext
from jobagent.domain.messages import UserMessage
from jobagent.domain.state import DialogueState
from jobagent.engine.clarify import ClarifyResponser
from jobagent.engine.dialogue_engine import DialogueEngine
from jobagent.plan.turn_plan import TurnPlan, TaskTurnPlan
from jobagent.plan.validator import TurnPlanValidator
from jobagent.prompts.loader import PromptLoader
from jobagent.task.action.builtin.action_response import ActionResponse
from jobagent.task.command.processor import CommandProcessor
from jobagent.task.flow.executor import FlowExecutor
from jobagent.task.flow.gates import FlowGate
from jobagent.task.handler import TaskHandler
from jobagent.task.loader import FlowLoader

FLOW_LIST = FlowLoader().load_many(["flow_config/user_flows.yml",
                                    "flow_config/system_flows.yml"])
INTENT = FLOW_LIST.get_flow_by_id("job_intent_collection")
QUESTION = INTENT.get_step_by_id("ask_position").response.text


class StubPlanner:
    def __init__(self, plan: TurnPlan):
        self.plan = plan

    async def predict(self, state, user_message, flow_list):
        return self.plan


def _engine(plan: TurnPlan) -> DialogueEngine:
    gate = FlowGate.from_flows(FLOW_LIST)
    return DialogueEngine(
        task_handler=TaskHandler(processor=CommandProcessor(), executor=FlowExecutor(),
                                 flow_list=FLOW_LIST,
                                 actions={"action_response": ActionResponse()}),
        planner=StubPlanner(plan),
        clarify=ClarifyResponser(PromptLoader()),
        validator=TurnPlanValidator(FLOW_LIST, flow_gate=gate),
        knowledge_handler=None,
        flow_gate=gate)


def _collecting_state(step_id: str = "ask_position") -> DialogueState:
    st = DialogueState(sender_id="u1")
    st.start_task(TaskContext(flow_id="job_intent_collection", step_id=step_id))
    step = INTENT.get_step_by_id(step_id)
    st.start_system_task(CollectedSystemContext(
        flow_id="system_collect_information",
        step_id=FLOW_LIST.get_flow_by_id("system_collect_information").get_start_step().id,
        response=asdict(step.response), slot_name=step.slot_name))
    return st


def _texts(result) -> list[str]:
    return [m.text for m in result.messages]


async def test_empty_plan_during_collect_reasks_instead_of_menu():
    """{} 落空时重问当前问句：流程接得下去，用户不会对着菜单发呆。"""
    engine = _engine(TurnPlan())
    result = await engine.handle_message(
        UserMessage(message_id="m1", text="继续求职意向"), _collecting_state())
    assert _texts(result) == [QUESTION]


async def test_resume_noop_during_collect_still_reasks():
    """resume_flow 打在活跃流程上是 no-op：系统过场重播首问，不能静默。"""
    plan = TurnPlan(task=TaskTurnPlan(commands=[{"type": "resume_flow"}]))
    result = await _engine(plan).handle_message(
        UserMessage(message_id="m2", text="继续求职意向"), _collecting_state())
    assert _texts(result) == [QUESTION]


async def test_locked_flow_whose_prereq_is_active_gets_guidance():
    """登录被门禁挡住且前置就是活跃流程：说清哪件事被卡住，而不是 no-op 空转。"""
    plan = TurnPlan(task=TaskTurnPlan(
        commands=[{"type": "start_flow", "flow_id": "boss_login"}]))
    result = await _engine(plan).handle_message(
        UserMessage(message_id="m3", text="扫码登录Boss直聘"), _collecting_state())
    text = _texts(result)[0]
    assert "BOSS直聘登录" in text and "前置" in text


async def test_idle_empty_plan_gets_actionable_clarify():
    """没在收集时落空：澄清要给可操作的下一步，不是纯能力清单。"""
    result = await _engine(TurnPlan()).handle_message(
        UserMessage(message_id="m4", text="你好"), DialogueState(sender_id="u1"))
    text = _texts(result)[0]
    assert "上传简历" in text and "回复「登录」" in text


def test_intent_flow_has_no_salary_step_and_cities_next_is_save():
    ids = [s.id for s in INTENT.steps]
    assert "ask_salary" not in ids
    cities = INTENT.get_step_by_id("ask_cities")
    assert [link.target for link in cities.next] == ["save_intent"]


def test_cities_validation_rejects_multiple_and_accepts_single():
    executor = FlowExecutor()
    step = INTENT.get_step_by_id("ask_cities")
    # 真实链路里 collect 二次触发前推进器已在 end 节点收掉系统过场，
    # 否则 _advance_flow_step 会写进系统上下文而不是业务流程
    st = _collecting_state("ask_cities")
    st.active_system_task = None
    st.active_task.slots["intent_cities"] = "杭州,郑州"
    call = executor._run_collect_step(st, step, FLOW_LIST)
    assert call is not None and call.name == "action_response"
    assert call.args["text"] == "一次只填一个城市哦，请回复一个城市名。"
    assert "intent_cities" not in st.active_task.slots   # 不合格的值要清掉重问

    st2 = _collecting_state("ask_cities")
    st2.active_system_task = None
    st2.active_task.slots["intent_cities"] = "杭州"
    assert executor._run_collect_step(st2, step, FLOW_LIST) is None
    assert st2.active_task.step_id == "save_intent"