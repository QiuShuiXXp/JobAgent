from typing import Any

from jobagent.domain.messages import BotMessage
from jobagent.domain.state import DialogueState
from jobagent.services.logout_service import LOGIN_SLOTS, LogoutService
from jobagent.task.action.base import Action, ActionResult


class ActionLogout(Action):
    """退出登录：profile cookie 与登录态槽位一次清干净，下次必须重新登录。"""

    name = "action_logout"

    def __init__(self, logout_service: LogoutService) -> None:
        self.logout_service = logout_service

    async def run(self, state: DialogueState, args: dict[str, Any]) -> ActionResult:
        result = await self.logout_service.logout(state.sender_id)
        state.drop_slots(*LOGIN_SLOTS)
        if not result["cookies_removed"]:
            return ActionResult(messages=[BotMessage(
                text="你当前本来就是未登录状态，需要筛岗或打招呼时说「登录」即可。")])
        return ActionResult(messages=[BotMessage(
            text=f"已退出 BOSS直聘登录（清掉 {result['cookies_removed']} 个 cookie）。"
                 f"求职意向、简历和已抓到的岗位都保留，下次要筛岗或打招呼前重新登录就行。")])
