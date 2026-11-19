"""CommandProcessor 的边界：恢复不存在/不指定流程、null 清槽跨中断栈、取消不并回全局。"""
from jobagent.config.paths import ASSETS_ROOT
from jobagent.domain.contexts import CanceledSystemContext, TaskContext
from jobagent.domain.messages import MessageType, UserMessage
from jobagent.domain.state import DialogueState
from jobagent.task.command.commands import CancelFlowCommand, ResumedFlowCommand, SetSlotsCommand, StartedFlowCommand
from jobagent.task.command.processor import CommandProcessor
from jobagent.task.loader import FlowLoader

FLOW_LIST = FlowLoader().load_many([ASSETS_ROOT / "flow_config" / "user_flows.yml",
                                    ASSETS_ROOT / "flow_config" / "system_flows.yml"])


def _state_with(flow_id="job_list_confirm", slots=None) -> DialogueState:
    state = DialogueState(sender_id="u1")
    state.start_task(TaskContext(flow_id=flow_id, step_id="start", slots=slots or {}))
    return state


def test_resume_without_id_on_empty_stack_is_noop():
    state = DialogueState(sender_id="u1")
    CommandProcessor().run(state, [ResumedFlowCommand()], FLOW_LIST)
    assert state.active_task is None and state.active_system_task is None


def test_cancel_login_turns_the_waiting_card_into_canceled():
    """不翻篇的话前端会一直轮询登录态，折叠按钮也被「登录中禁用」锁死。"""
    state = _state_with(flow_id="boss_login")
    state.begin_turn(UserMessage(message_id="m1", type=MessageType.TEXT, text="算啦不登录了"))
    CommandProcessor().run(state, [CancelFlowCommand()], FLOW_LIST)
    cards = [m.object for m in state.pending_turn.bot_messages if m.object is not None]
    assert any(c.type == "login_window" and c.payload.get("status") == "canceled"
               for c in cards)


def test_cancel_other_flow_does_not_touch_login_cards():
    state = _state_with(flow_id="job_list_confirm")
    state.begin_turn(UserMessage(message_id="m1", type=MessageType.TEXT, text="取消"))
    CommandProcessor().run(state, [CancelFlowCommand()], FLOW_LIST)
    assert all(m.object is None or m.object.type != "login_window"
               for m in state.pending_turn.bot_messages)


def test_resume_unknown_flow_keeps_the_interrupted_task():
    """目标不在栈里时不能崩，也不能把用户弄成“没有活跃流程”。"""
    state = _state_with()
    state.interrupted_active_tasks.append(
        TaskContext(flow_id="boss_login", step_id="start", slots={}))
    CommandProcessor().run(state, [ResumedFlowCommand(flow_id="nope")], FLOW_LIST)
    assert state.active_task.flow_id == "job_list_confirm"
    assert [t.flow_id for t in state.interrupted_active_tasks] == ["boss_login"]


def test_null_slot_clears_active_interrupted_and_global():
    state = _state_with(slots={"login_status": "OK"})
    state.interrupted_active_tasks.append(
        TaskContext(flow_id="boss_login", step_id="start", slots={"login_status": "OK"}))
    state.global_slots["login_status"] = "OK"
    CommandProcessor().run(state, [SetSlotsCommand(slots={"login_status": None})], FLOW_LIST)
    assert state.active_task.slots == {}
    assert state.interrupted_active_tasks[0].slots == {}
    assert "login_status" not in state.global_slots


def test_cancel_discards_partial_slots_without_touching_global():
    state = _state_with(flow_id="job_intent_collection", slots={"intent_cities": "上海"})
    state.global_slots["intent_cities"] = "杭州"
    CommandProcessor().run(state, [CancelFlowCommand()], FLOW_LIST)
    assert state.active_task is None
    assert state.global_slots["intent_cities"] == "杭州", "取消的修改不能污染全局记忆"
    assert isinstance(state.active_system_task, CanceledSystemContext)


def test_reupload_clears_previous_confirmation():
    """上一份简历确认完后标记并进了全局记忆；新简历上传必须清掉它，
    否则新流程播种旧值，collect 见值直接放行，确认问句和按钮都被跳过。"""
    state = DialogueState(sender_id="u1")
    state.global_slots.update({"resume_confirmed": "确认", "resume_file_ref": "old.pdf"})
    CommandProcessor().run(state, [
        StartedFlowCommand(flow_id="resume_upload_confirm"),
        SetSlotsCommand(slots={"resume_file_ref": "new.pdf", "resume_confirmed": None}),
    ], FLOW_LIST)
    assert state.get_slots("resume_confirmed") is None
    assert state.get_slots("resume_file_ref") == "new.pdf"
