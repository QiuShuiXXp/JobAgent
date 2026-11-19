"""真人登录两个动作的单测：不起 Chrome，只看把页面摆到人眼前/查 cookie/收口这条走法。"""
import pytest

from jobagent.api.errors import AppError, ErrorCode
from jobagent.domain.state import DialogueState
from jobagent.infrastructure.browser import PageTimeout
from jobagent.task.action.customer.action_login import (
    ActionConfirmLogin, ActionOpenLogin, LOGIN_CONFIRMED, LOGIN_URL,
)


class FakeLoginBrowser:
    """登录动作要的浏览器接口：把页面摆到人眼前、导航、查 cookie 各自计数。

    get_context 会把"实例存在"置真（真实管理器就是这样：起了实例 has_context 才为真）。
    摆出来是单向的，所以 fake 里也只有 present 会改 _presented，release 不动它。
    """

    def __init__(self, *, logged_in: bool = False, opened: bool = True,
                 timeout_on_goto: bool = False):
        self._logged_in = logged_in
        self._opened = opened
        self._presented = opened          # 有实例且没被收过 = 页面正在人眼前
        self.timeout_on_goto = timeout_on_goto
        self.presented: list[bool] = []
        self.released = 0
        self.calls: list[str] = []

    def has_context(self, uid):
        return self._opened

    def is_presented(self):
        return self._presented

    def present(self):
        self.calls.append("present")
        self.presented.append(True)
        self._presented = True
        return True

    async def get_context(self, uid):
        self.calls.append("get_context")
        self._opened = True
        return object()

    async def guarded_goto(self, page, url, **kwargs):
        self.calls.append(f"goto:{url}")
        if self.timeout_on_goto:
            raise PageTimeout(url)
        return page

    async def tab_url(self, page):
        return LOGIN_URL

    async def logged_in(self, uid):
        return self._logged_in

    async def release_context(self, uid):
        self.calls.append("release")
        self.released += 1
        self._opened = False


def _state(**slots) -> DialogueState:
    state = DialogueState(sender_id="u1")
    state.global_slots.update(slots)
    return state


def _texts(result):
    return [m.text for m in result.messages if m.text]


def _card(result):
    obj = result.messages[0].object
    return obj.type, obj.payload["status"]


async def test_open_login_requires_confirmed_intent():
    action = ActionOpenLogin(FakeLoginBrowser())
    with pytest.raises(AppError) as e:
        await action.run(_state(), {})
    assert e.value.error is ErrorCode.INTENT_NOT_CONFIRMED


async def test_confirm_login_also_requires_confirmed_intent():
    """门禁不只上在第一轮：两个动作看到的应该是同一套前置条件。"""
    with pytest.raises(AppError) as e:
        await ActionConfirmLogin(FakeLoginBrowser(logged_in=True)).run(_state(), {})
    assert e.value.error is ErrorCode.INTENT_NOT_CONFIRMED


async def test_open_login_presents_the_page_before_navigating():
    """先探 cookie 起了实例，确认没登录才摆出来+导航：present 必须排在 goto 之前。"""
    browser = FakeLoginBrowser(opened=False)
    result = await ActionOpenLogin(browser).run(_state(intent_confirmed="yes"), {})
    assert browser.calls == ["get_context", "get_context", "present", f"goto:{LOGIN_URL}"]
    assert browser.presented == [True]
    assert _card(result) == ("login_window", "waiting")
    assert browser.released == 0                      # 页面要留在人面前


async def test_open_login_probes_cookies_without_opening_a_page():
    """没实例时先起一个只读 cookie：不摆页面也不导航，免得给已登录的人看登录页。"""
    browser = FakeLoginBrowser(opened=False, logged_in=True)
    state = _state(intent_confirmed="yes")
    result = await ActionOpenLogin(browser).run(state, {})
    assert browser.calls == ["get_context"]
    assert browser.presented == []                    # 压根没要求过摆出来
    assert _card(result) == ("login_window", "logged_in")
    assert state.get_slots("login_status") == "OK"


async def test_open_login_short_circuits_when_already_logged_in():
    browser = FakeLoginBrowser(logged_in=True)
    state = _state(intent_confirmed="yes")
    result = await ActionOpenLogin(browser).run(state, {})
    assert browser.calls == []                        # 不摆页面、不导航、也不回收
    assert _card(result) == ("login_window", "logged_in")
    assert state.get_slots("login_status") == "OK"


async def test_open_login_releases_page_when_it_never_loads():
    browser = FakeLoginBrowser(timeout_on_goto=True)
    with pytest.raises(AppError) as e:
        await ActionOpenLogin(browser).run(_state(intent_confirmed="yes"), {})
    assert e.value.error is ErrorCode.LOGIN_PAGE_TIMEOUT
    assert browser.released == 1                      # 打不开的页面要收掉


async def test_confirm_login_keeps_the_page_and_unlocks_search():
    """登录成功不再关窗：浏览器保持开着，下一步抓岗位直接用同一个实例。"""
    browser = FakeLoginBrowser(logged_in=True)
    state = _state(intent_confirmed="yes")
    result = await ActionConfirmLogin(browser).run(state, {})
    assert browser.released == 0
    assert _card(result) == ("login_window", "logged_in")
    assert state.get_slots("login_status") == "OK"


async def test_confirm_login_keeps_notice_when_user_still_working():
    """没登录且页面还在人眼前：只提醒，不重新导航也不回收——人可能正在填。"""
    browser = FakeLoginBrowser(logged_in=False, opened=True)
    result = await ActionConfirmLogin(browser).run(_state(intent_confirmed="yes"), {})
    assert browser.released == 0
    assert browser.presented == []                    # 一次 present 都没做
    assert "还没检测到登录态" in " ".join(_texts(result))
    assert result.slot_updates == {}


async def test_confirm_login_presents_again_when_page_was_hidden():
    """页面被收走了：重新摆出来并导航，同时作废卡片上留着的「已登录」标记。"""
    browser = FakeLoginBrowser(logged_in=False, opened=False)
    state = _state(intent_confirmed="yes")
    result = await ActionConfirmLogin(browser).run(state, {})
    assert browser.presented == [True]
    assert _card(result) == ("login_window", "waiting")
    assert result.slot_updates == {"login_confirmed": None}


def test_login_confirmed_value_is_what_card_prefills():
    """前端自动回传写的值必须和 collect 槽校验的一致。"""
    assert LOGIN_CONFIRMED == "已登录"
