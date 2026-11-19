"""
真人登录：把 BOSS直聘登录页交给人，读 profile cookie 判定成没成。

窗口里用 APP 扫码、手机号、密码，任何一种都行；判定只看 wt2/bst/zp_at 三个 auth
cookie（profile 级，tab 被页面关掉也读得到）。
"""
import logging
from typing import Any

from jobagent.api.errors import AppError, ErrorCode
from jobagent.config.settings import settings
from jobagent.domain.messages import BotMessage, MessageType, FocusedObject
from jobagent.domain.state import DialogueState
from jobagent.infrastructure.browser import BrowserManager, PageTimeout
from jobagent.task.action.base import Action, ActionResult

logger = logging.getLogger("jobagent.action.login")

LOGIN_URL = f"{settings.platform_base_url}/web/user/"
# 前端轮询检测到登录态后经 login_window 卡片自动回传，collect 节点按此值预填
LOGIN_CONFIRMED = "已登录"
WINDOW_TEXT = ("你还没有登录 BOSS直聘，需要先登录才能筛选岗位。"
               "登录页已经打开：在那个页面里登录，APP 扫码、手机号、密码都行；"
               "出现安全验证就顺手完成它。登录成功后我接着给你筛岗位，这块页面不用你关。")


def _login_card(status: str) -> BotMessage:
    """状态卡片：前端据此显示"等待登录/已登录"；登录完成由轮询自动确认，只在页面没显示时给一个「打开登录页」按钮。

    status 只用于展示；登录与否由后端的 cookie 判定说了算，别拿前端回传当依据。
    """
    return BotMessage(
        type=MessageType.OBJECT,
        object=FocusedObject(type="login_window", id="login_window",
                             payload={"status": status}))


class _LoginBase(Action):
    """两轮共用：门禁 / 把登录页交给人 / 查 cookie / 已登录收口。"""

    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser

    @staticmethod
    def _require_intent(state: DialogueState) -> None:
        """流程条件边已挡一次，两个动作各自再兜一次：门禁不能只上在第一轮。"""
        if state.get_slots("intent_confirmed") != "yes":
            raise AppError(ErrorCode.INTENT_NOT_CONFIRMED,
                           "请先确认求职意向，再登录 BOSS直聘", http_status=403)

    async def _already_logged_in(self, sid: str) -> bool:
        """读 cookie 判登录态，必要时先把实例拉起来。

        auth cookie 存在 profile 里，但只有活实例读得到；没有实例时先起一个——
        不导航任何页面，所以已经登录的用户根本不会看到登录页。
        """
        if not self.browser.has_context(sid):
            await self.browser.get_context(sid)
        return await self.browser.logged_in(sid)

    async def _show_login_window(self, sid: str):
        """把该用户的浏览器停在登录页、并且让人看得见，返回那个 tab。

        先拿实例再 present：present 只负责把这块屏幕挪到人眼前（桌面壳展开左栏 /
        本机 Chrome 窗口本来就在），页面归后端导航。
        """
        page = await self.browser.get_context(sid)
        self.browser.present()
        await self.browser.guarded_goto(page, LOGIN_URL, timeout_s=30)
        return page

    async def _finish_logged_in(self, state: DialogueState, text: str) -> ActionResult:
        """已登录收口：浏览器保持开着（不关也不藏），下一步抓岗位直接用同一个实例。

        同时回写 login_confirmed：流程的确认节点靠它跳过“去登录页完成登录”的提问。
        """
        state.set_slots({"login_status": "OK"})
        return ActionResult(
            messages=[_login_card("logged_in"), BotMessage(text=text)],
            slot_updates={"login_confirmed": LOGIN_CONFIRMED})

    async def _waiting(self, state: DialogueState, text: str) -> ActionResult:
        """重开登录窗继续等：清掉确认槽，否则 collect 节点见槽有值会直接放行。"""
        await self._show_login_window(state.sender_id)
        return ActionResult(messages=[_login_card("waiting"), BotMessage(text=text)],
                            slot_updates={"login_confirmed": None})


class ActionOpenLogin(_LoginBase):
    """
    登录第一轮：先查后登，没登录就把登录页开窗交出去，立刻返回。

    不轮询、不释放浏览器：确认交给 action_confirm_login，
    中间这段等待由前端轮 /api/login_status 完成，不占 /api/chat 的往返。
    """
    name = "action_open_login"

    async def run(self, state: DialogueState, args: dict[str, Any]) -> ActionResult:
        sid = state.sender_id
        self._require_intent(state)
        if await self._already_logged_in(sid):
            return await self._finish_logged_in(
                state, "浏览器里已经是登录状态，不用重复登录；登录态已确认。")
        try:
            page = await self._show_login_window(sid)
        except PageTimeout as e:
            await self.browser.release_context(sid)
            raise AppError(ErrorCode.LOGIN_PAGE_TIMEOUT,
                           "BOSS直聘登录页 30 秒内没打开：回一句「重新登录」我再开一次",
                           http_status=502) from e
        # 开窗后被风控拦到验证页也照常返回：窗口本来就在人面前，验证就在同一个窗里做
        logger.info("已为用户 %s 打开登录页：%s", sid, await self.browser.tab_url(page))
        return ActionResult(messages=[_login_card("waiting"), BotMessage(text=WINDOW_TEXT)])


class ActionConfirmLogin(_LoginBase):
    """
    登录第二轮：读 profile 的 auth cookie 判定成败。

    成：login_status=OK，页面保持打开接着用。
    没成：页面还在人眼前就说明人还没弄完，只提醒不动它；不在了才重新调出来一次。
    """
    name = "action_confirm_login"

    async def run(self, state: DialogueState, args: dict[str, Any]) -> ActionResult:
        sid = state.sender_id
        self._require_intent(state)
        # 页面可能被登录跳转带跑了：cookie 在 profile 里就算登录成功，不必再展开一次
        if await self._already_logged_in(sid):
            return await self._finish_logged_in(
                state, "登录成功，接下来你可以说【筛选匹配岗位】，我将为你筛选岗位。")
        if not self.browser.is_presented():
            return await self._waiting(
                state, "登录页面已经不显示了：已重新调出来，请在页面里完成登录，我再确认一次。")
        return ActionResult(messages=[
            _login_card("waiting"),
            BotMessage(text="还没检测到登录态：请在打开的页面里把登录走完"
                            "（有安全验证就一起做掉），完成后我自动继续。"),
        ])
