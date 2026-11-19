"""动作注册与分派单测（注册表已并入 TaskHandler）。"""
from types import SimpleNamespace
from dataclasses import dataclass

import pytest

from jobagent.api.errors import AppError
from jobagent.domain.contexts import TaskContext
from jobagent.domain.state import DialogueState
from jobagent.task.flow.gates import FlowGate
from jobagent.task.action.base import Action, ActionCall, ActionResult
from jobagent.task.action.builder import _register, build_actions
from jobagent.task.action.builtin.action_listen import ActionListen
from jobagent.task.action.builtin.action_response import ActionResponse
from jobagent.task.command.processor import CommandProcessor
from jobagent.task.flow.executor import FlowExecutor
from jobagent.task.flow.flows import FlowsList
from jobagent.task.handler import TaskHandler


def _handler(actions: dict) -> TaskHandler:
    return TaskHandler(executor=FlowExecutor(), processor=CommandProcessor(),
                       flow_list=FlowsList(), actions=actions)


@dataclass(slots=True)
class _FakeSystemCtx:
    response: str = ""


def _fake_state(slots=None, response="请提供意向岗位"):
    slots = slots if slots is not None else {}
    state = SimpleNamespace(
        active_task=SimpleNamespace(slots=slots),
        slots_view=slots,                  # DialogueState.slots_view：渲染用的合并视图
        active_system_task=_FakeSystemCtx(response=response),   # dataclass：asdict 可用
        sender_id="u1",
    )
    return state


class _DummyAction(Action):
    name = "action_dummy"

    async def run(self, state, action_args):
        return ActionResult(messages=[])


def test_register_duplicate_fail_fast():
    actions = {}
    _register(actions, _DummyAction())
    with pytest.raises(ValueError):
        _register(actions, _DummyAction())


def test_build_actions_registers_builtin():
    actions = build_actions()
    assert "action_response" in actions
    assert "action_listen" in actions


async def test_runner_unknown_action_raises_app_error():
    with pytest.raises(AppError):
        await _handler({}).run(ActionCall(name="action_not_exist"), _fake_state())


async def test_runner_dispatches_registered_action():
    result = await _handler({"action_response": ActionResponse()}).run(
        ActionCall(name="action_response", args={"text": "hi"}), _fake_state())
    assert result.messages[0].text == "hi"


async def test_action_response_static_with_slot_interp():
    state = _fake_state(slots={"position": "Python"})
    result = await ActionResponse().run(state, {"text": "求职意向：{{ slots.position }} 已收到"})
    assert result.messages[0].text == "求职意向：Python 已收到"


async def test_action_response_rephrase_falls_back_to_static():
    state = _fake_state(slots={"position": "Python"})
    result = await ActionResponse().run(state, {"mode": "rephrase", "text": "意向 {{ slots.position }}"})
    assert result.messages[0].text == "意向 Python"


async def test_action_listen_returns_empty():
    result = await ActionListen().run(_fake_state(), {})
    assert result.messages == [] and result.slot_updates == {}


async def test_action_response_when_active_task_is_system_context():
    """cancel_flow 把过场上下文挂在 active_task 上，它没有 slots，渲染不能崩。"""
    state = SimpleNamespace(active_task=_FakeSystemCtx(response=""), slots_view={},
                            active_system_task=None, sender_id="u1")
    result = await ActionResponse().run(state, {"text": "好的，先帮你取消当前事项。"})
    assert result.messages[0].text == "好的，先帮你取消当前事项。"


def test_slots_view_merges_global_interrupted_and_active():
    """规划器、条件边、话术渲染必须看到同一份槽位：后层覆盖前层。"""
    state = DialogueState(sender_id="u1")
    state.global_slots.update({"a": "global", "b": "global"})
    state.interrupted_active_tasks.append(
        TaskContext(flow_id="f1", step_id="s1", slots={"a": "interrupted"}))
    state.start_task(TaskContext(flow_id="f2", step_id="s0", slots={"b": "active"}))
    assert state.slots_view == {"a": "interrupted", "b": "active"}


def test_interrupted_task_does_not_count_as_completed():
    """DAG 门禁：被中断的流程没走到 end，不算完成（与槽位驱动语义不同）。"""
    from jobagent.config.paths import ASSETS_ROOT
    from jobagent.task.loader import FlowLoader
    flow_list = FlowLoader().load_many([
        ASSETS_ROOT / "flow_config" / "user_flows.yml",
        ASSETS_ROOT / "flow_config" / "system_flows.yml"])
    gate = FlowGate.from_flows(flow_list)
    state = DialogueState(sender_id="u1")
    state.interrupted_active_tasks.append(
        TaskContext(flow_id="job_intent_collection", step_id="ask_cities", slots={}))
    assert not gate.is_unlocked(state.completed_flows, "boss_login")