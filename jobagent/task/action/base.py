from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from jobagent.api.errors import AppError, ErrorCode
from jobagent.domain.messages import BotMessage
from jobagent.domain.state import DialogueState


@dataclass(slots=True)
class ActionCall:
    name: str                          # 动作名（注册表键）
    args: dict[str, Any] = field(default_factory=dict)   # YAML args（jinja2 模板语法，action_response 渲染）


@dataclass(slots=True)
class ActionResult:
    messages: list[BotMessage] = field(default_factory=list)   # 本动作产出的回复
    slot_updates: dict[str, Any] = field(default_factory=dict) # 回写槽位


class Action(ABC):
    name: str = ""

    @abstractmethod
    async def run(self, state: DialogueState, args: dict[str, Any]) -> ActionResult:
        ...

    @staticmethod
    def require_login(state: DialogueState) -> None:
        """登录门禁。流程的条件边已经挡过一轮，动作入口再兜一次。

        要兜的是这个：login_status 会被动作之外的事作废（退出登录、profile cookie 过期），
        而规划器可能拿着上一轮残留的槽位直接选中动作，只靠流程边挡不住。
        """
        if state.get_slots("login_status") != "OK":
            raise AppError(ErrorCode.LOGIN_INVALIDATED, "请先登录 BOSS直聘", http_status=403)
