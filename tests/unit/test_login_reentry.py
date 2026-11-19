"""登录流程重进/恢复的门禁与开窗单测（不碰 LLM、不碰浏览器）。"""
import pytest

from jobagent.api.errors import AppError, ErrorCode
from jobagent.config.paths import ASSETS_ROOT
from jobagent.domain.contexts import CanceledSystemContext, TaskContext
from jobagent.domain.state import DialogueState
from jobagent.task.flow.gates import FlowGate
from jobagent.task.action.customer.action_search_jobs import ActionSearchJobs
from jobagent.task.command.commands import CancelFlowCommand, StartedFlowCommand
from jobagent.task.command.processor import CommandProcessor
from jobagent.task.loader import FlowLoader

FLOW_LIST = FlowLoader().load_many([
    ASSETS_ROOT / "flow_config" / "user_flows.yml",
    ASSETS_ROOT / "flow_config" / "system_flows.yml"])


def _parked_at_confirm() -> DialogueState:
    """停在 confirm_login、手上还留着上一轮自动确认标记的登录流程。"""
    state = DialogueState(sender_id="u1")
    state.global_slots["intent_confirmed"] = "yes"
    state.start_task(TaskContext(flow_id="boss_login", step_id="confirm_login",
                                 slots={"intent_confirmed": "yes",
                                        "login_confirmed": "已登录"}))
    return state


def test_reentry_restarts_login_flow_from_start():
    """窗口早被关掉了：重进必须回到 start 重新开窗，而不是把「登录好了叫我」再念一遍。"""
    state = _parked_at_confirm()
    CommandProcessor().run(state, [StartedFlowCommand(flow_id="boss_login")], FLOW_LIST)
    assert state.active_task.step_id == "start"
    # 旧卡片的确认标记一起作废，否则会从开窗直接跳去校验
    assert state.get_slots("login_confirmed") is None


def test_resume_from_stack_also_restarts():
    """中断后恢复（「恢复登录流程不弹窗」那条路径）同样回到 start。"""
    state = _parked_at_confirm()
    state.interrupt_active_task()
    state.active_system_task = None
    CommandProcessor().run(state, [StartedFlowCommand(flow_id="boss_login")], FLOW_LIST)
    assert state.active_task.step_id == "start"
    assert state.interrupted_active_tasks == []


def test_plain_flow_reentry_stays_put():
    """没标 restart_on_reentry 的流程不许被这套逻辑改动——筛岗重进不该重新搜一遍站。"""
    state = DialogueState(sender_id="u1")
    state.start_task(TaskContext(flow_id="job_list_confirm", step_id="collect_selection",
                                 slots={}))
    CommandProcessor().run(state, [StartedFlowCommand(flow_id="job_list_confirm")], FLOW_LIST)
    assert state.active_task.step_id == "collect_selection"


def test_cancel_flow_uses_system_task_not_business_task():
    """取消过场只进 active_system_task；active_task 永远是业务任务（或 None）。"""
    state = DialogueState(sender_id="u1")
    state.start_task(TaskContext(flow_id="job_list_confirm", step_id="start", slots={}))
    CommandProcessor().run(state, [CancelFlowCommand()], FLOW_LIST)
    assert state.active_task is None
    assert isinstance(state.active_system_task, CanceledSystemContext)


GATE = FlowGate.from_flows(FLOW_LIST)

def test_unlock_target_points_to_login_when_only_login_missing():
    state = DialogueState(sender_id="u1")
    state.completed_flows.add("job_intent_collection")
    assert GATE.unlock_target(state.completed_flows, "job_list_confirm") == "boss_login"


def test_unlock_target_points_to_intent_when_nothing_filled():
    state = DialogueState(sender_id="u1")
    assert GATE.unlock_target(state.completed_flows, "job_list_confirm") == "job_intent_collection"


# ---- 动作入口的门禁：流程条件边之外再兜一次（规划器可能拿着过期槽位直调）----

def _search_state(**slots) -> DialogueState:
    state = DialogueState(sender_id="u1")
    state.global_slots.update({"resume_confirmed": "确认", "intent_position": "Python 后端",
                               "intent_cities": "杭州, 郑州", **slots})
    return state


def _gates(state: DialogueState):
    return ActionSearchJobs(None, None)._gates(state)   # 门禁只读 state，两个依赖用不上


def test_search_gate_returns_position_and_cities_when_logged_in():
    assert _gates(_search_state(login_status="OK")) == ("Python 后端", ["杭州", "郑州"])


def test_search_gate_blocks_when_login_status_went_stale():
    with pytest.raises(AppError) as e:
        _gates(_search_state(login_status=""))
    assert e.value.error is ErrorCode.LOGIN_INVALIDATED


def test_search_gate_blocks_unconfirmed_resume():
    with pytest.raises(AppError) as e:
        _gates(_search_state(login_status="OK", resume_confirmed=""))
    assert e.value.error is ErrorCode.RESUME_NOT_CONFIRMED


def test_search_gate_blocks_missing_position():
    with pytest.raises(AppError) as e:
        _gates(_search_state(login_status="OK", intent_position="   "))
    assert e.value.error is ErrorCode.INTENT_NOT_CONFIRMED
