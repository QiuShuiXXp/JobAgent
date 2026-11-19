"""澄清器：规划结果不能用时，给用户一句能照做的下一步。

只有两句话术：
- 没听懂 / 说不清（绝大多数 reason）→ clarify_unrecognized.j2，告诉用户现在能干什么。
- 门禁挡住且找不到该替他开的前置流程 → clarify_flow_locked.j2，说清是哪件事被卡住。
"""
from jobagent.domain.messages import BotMessage
from jobagent.plan.turn_plan import ClarifyReason
from jobagent.prompts.loader import PromptLoader

UNRECOGNIZED = "clarify_unrecognized.j2"
FLOW_LOCKED = "clarify_flow_locked.j2"


class ClarifyResponser:

    def __init__(self, loader: PromptLoader) -> None:
        self.loader = loader

    async def respond(self, reason: ClarifyReason, flow_name: str = "") -> BotMessage:
        """flow_name 只有 FLOW_LOCKED 用得上；其余 reason 的文案与用户说了什么无关。"""
        if reason is ClarifyReason.FLOW_LOCKED:
            return BotMessage(text=self.loader.render(FLOW_LOCKED, flow_name=flow_name))
        return BotMessage(text=self.loader.render(UNRECOGNIZED))