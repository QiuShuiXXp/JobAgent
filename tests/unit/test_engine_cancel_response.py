"""回归：取消登录的翻篇卡片必须出现在 HTTP 响应里，而不只是落盘 state。

曾经处理器把卡片追加进 pending_turn，引擎却只回 executor 的 messages，
前端收不到卡片，折叠按钮被「登录中禁用」永久锁死。
"""
from types import SimpleNamespace

from jobagent.config.paths import ASSETS_ROOT
from jobagent.domain.contexts import TaskContext
from jobagent.domain.messages import MessageType, UserMessage
from jobagent.domain.state import DialogueState
from jobagent.engine.dialogue_engine import DialogueEngine
from jobagent.plan.turn_plan import TaskTurnPlan, TurnPlan, TurnPlanValidateResult
from jobagent.task.action.builtin.action_response import ActionResponse
from jobagent.task.command.processor import CommandProcessor
from jobagent.task.flow.executor import FlowExecutor
from jobagent.task.handler import TaskHandler
from jobagent.task.loader import FlowLoader

FLOW_LIST = FlowLoader().load_many([ASSETS_ROOT / "flow_config" / "user_flows.yml",
                                    ASSETS_ROOT / "flow_config" / "system_flows.yml"])


class _Planner:
    async def predict(self, state, user_message, flow_list):
        return TurnPlan(task=TaskTurnPlan(commands=[{"type": "cancel_flow"}]))


class _Validator:
    def validate(self, turn_plan, state):
        return TurnPlanValidateResult(valid=True)


def _engine() -> DialogueEngine:
    handler = TaskHandler(FlowExecutor(), CommandProcessor(), FLOW_LIST,
                          {"action_response": ActionResponse()})
    return DialogueEngine(task_handler=handler, planner=_Planner(), clarify=None,
                          validator=_Validator(), knowledge_handler=None)


async def test_cancel_login_card_is_in_the_response_not_only_in_state():
    state = DialogueState(sender_id="u1")
    state.start_task(TaskContext(flow_id="boss_login", step_id="start", slots={}))
    result = await _engine().handle_message(
        UserMessage(message_id="m1", type=MessageType.TEXT, text="取消登录"), state)
    cards = [m.object for m in result.messages if m.object is not None]
    assert any(c.type == "login_window" and c.payload.get("status") == "canceled"
               for c in cards), "翻篇卡片没进响应，前端折叠按钮会永久锁死"
    # 播报文本也还在，顺序：卡片在前、过场白在后
    assert result.messages[-1].text == "好的，先帮你取消当前事项。"