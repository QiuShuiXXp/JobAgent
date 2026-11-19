"""FlowExecutor（参考实现语义）：listen 只推进不执行、collect 失败补话术、action 后不挂条件边。"""
from jobagent.domain.contexts import TaskContext
from jobagent.domain.messages import BotMessage
from jobagent.domain.state import DialogueState
from jobagent.task.action.base import ActionResult
from jobagent.task.flow.executor import FlowExecutor
from jobagent.task.flow.flows import Flow, FlowsList
from jobagent.task.flow.links import ConditionLink, StaticLink
from jobagent.task.flow.steps import (
    ActionFlowStep, CollectFlowStep, EndFlowStep, FlowStepType, ResponseDefinition,
    SlotValidation, StartFlowStep,
)
from jobagent.task.loader import FlowLoader
from jobagent.config.paths import ASSETS_ROOT


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def run(self, call, state):
        self.calls.append(call.name)
        if call.name == "action_response":
            return ActionResult(messages=[BotMessage(text=call.args.get("text", ""))])
        return ActionResult(messages=[])


def _action(step_id, action, next_links, args=None):
    return ActionFlowStep(id=step_id, type=FlowStepType.ACTION, action=action,
                          args=args or {}, next=next_links)


def _start(step_id, target):
    return StartFlowStep(id=step_id, type=FlowStepType.START, next=[StaticLink(target)])


def _end(step_id):
    return EndFlowStep(id=step_id, type=FlowStepType.END, next=[])


def _state(flow_id="f", step_id="start"):
    state = DialogueState(sender_id="u1")
    state.start_task(TaskContext(flow_id=flow_id, step_id=step_id))
    return state


def test_boss_login_has_no_condition_edges_after_actions():
    """参考执行器先推进再执行 action；action 后面挂条件边会读到旧槽位。"""
    flows = FlowLoader().load_many([ASSETS_ROOT / "flow_config" / "user_flows.yml",
                                    ASSETS_ROOT / "flow_config" / "system_flows.yml"])
    flow = flows.get_flow_by_id("boss_login")
    for step_id in ("do_login", "check_login"):
        step = flow.get_step_by_id(step_id)
        assert step is not None
        assert all(not isinstance(link, ConditionLink) for link in step.next), \
            f"{step_id} 的出口不能是条件边"


async def test_listen_advances_but_does_not_execute():
    flow = Flow(flow_id="f", flow_name="f", description="", steps=[
        _start("start", "wait"),
        _action("wait", "action_listen", [StaticLink("end")]),
        _end("end"),
    ])
    runner = FakeRunner()
    state = _state()
    await FlowExecutor().execute_flow(state, FlowsList(flows=[flow]), runner)
    assert runner.calls == [], "listen 不该真的执行"
    assert state.active_task.step_id == "end", "listen 要先推进，下一轮才能从 end 收尾"


async def test_collect_failure_plays_failure_response_then_reasks():
    system_flow = Flow(flow_id="system_collect_information", flow_name="collect", description="",
                       steps=[_start("start", "wait"),
                              _action("wait", "action_listen", [StaticLink("end")]),
                              _end("end")])
    flow = Flow(flow_id="f", flow_name="f", description="", steps=[
        _start("start", "ask"),
        CollectFlowStep(id="ask", type=FlowStepType.COLLECT, slot_name="x",
                        response=ResponseDefinition(text="问题"),
                        validation=SlotValidation(
                            condition="slots.get('x') == 'ok'",
                            failure_response=ResponseDefinition(text="填错了")),
                        next=[StaticLink("end")]),
        _end("end"),
    ])
    state = _state()
    state.set_slots({"x": "no"})
    msgs = await FlowExecutor().execute_flow(state, FlowsList(flows=[flow, system_flow]), FakeRunner())
    assert [m.text for m in msgs] == ["填错了"]
    assert state.get_slots("x") is None, "校验失败要删槽"
    assert state.active_system_task is not None, "删槽后应重新进入收集首问"


async def test_collect_question_carries_choices():
    """YAML 配的快捷回复要挂在首问消息上发给前端，且序列化往返不丢。"""
    from jobagent.task.action.builtin.action_response import ActionResponse

    flows = FlowLoader().load_many([ASSETS_ROOT / "flow_config" / "user_flows.yml",
                                    ASSETS_ROOT / "flow_config" / "system_flows.yml"])

    class ChoiceRunner:
        async def run(self, call, state):
            if call.name == "action_response":
                return await ActionResponse().run(state, call.args)
            return ActionResult(messages=[])

    state = DialogueState(sender_id="u1")
    state.start_task(TaskContext(flow_id="resume_upload_confirm", step_id="ask_confirm"))
    msgs = await FlowExecutor().execute_flow(state, flows, ChoiceRunner())
    assert len(msgs) == 1
    assert msgs[0].choices == ["确认", "重新上传"]
    assert BotMessage.from_dict(msgs[0].to_dict()).choices == ["确认", "重新上传"]


async def test_boss_login_already_logged_in_skips_prompt_and_ends():
    """已登录时 action 回写 login_confirmed，确认节点直接跳过提问，check_login 后收尾。"""
    flows = FlowLoader().load_many([ASSETS_ROOT / "flow_config" / "user_flows.yml",
                                    ASSETS_ROOT / "flow_config" / "system_flows.yml"])
    state = DialogueState(sender_id="u1")
    state.start_task(TaskContext(flow_id="boss_login", step_id="start",
                                 slots={"intent_confirmed": "yes"}))

    class LoginRunner:
        def __init__(self):
            self.calls = []

        async def run(self, call, state):
            self.calls.append(call.name)
            if call.name == "action_open_login":
                return ActionResult(messages=[BotMessage(text="已经是登录状态")],
                                    slot_updates={"login_confirmed": "已登录",
                                                  "login_status": "OK"})
            return ActionResult(messages=[BotMessage(text="登录成功")])

    runner = LoginRunner()
    await FlowExecutor().execute_flow(state, flows, runner)
    assert runner.calls == ["action_open_login", "action_confirm_login"]
    assert state.active_task is None, "流程应走到 end"

