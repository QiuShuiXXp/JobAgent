"""风控人工介入单测：不起 Chrome，只验走法。形态差异的测试在 test_viewport.py。"""
import pytest

import jobagent.infrastructure.browser as browser_mod
from jobagent.api.errors import AppError, ErrorCode
from jobagent.infrastructure.browser import BrowserManager
from jobagent.infrastructure.risk import (
    STILL_BLOCKED_HINT, RiskBlocked, api_risk, is_blocked_url, risk_code_of,
)
from jobagent.infrastructure.viewport import LocalChrome
from jobagent.task.action.customer.action_search_jobs import ActionSearchJobs

# 2026-10-17 实测：账号被限制成 32 档时，BOSS 不跳页也不报错，只在接口包里回这两样
BANNED_BODY = {"code": 32, "message": "您的账户存在异常行为，已暂时被禁止使用.", "zpData": []}
BANNED_URL = ("https://www.zhipin.com/web/passport/zp/403.html?callbackUrl="
              "https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fgeek%2Fjobs%3Fquery%3DPython%26city%3D101210100"
              "&appName=zhipin_geek_spa_web&code=32")


class FakeChromium:
    """顶替 DrissionPage 的 Chromium：只需要 latest_tab 和 quit。"""

    def __init__(self, tab):
        self.latest_tab = tab

    def quit(self):
        return None


@pytest.fixture
def manager(monkeypatch):
    """dev 形态的管理器 + 打桩的启动/导航（视口本身的测试在 test_viewport.py）。"""
    monkeypatch.setattr(browser_mod, "VERIFY_POLL_S", 0)
    m = BrowserManager(LocalChrome(headless=True))
    # 带 callbackUrl 的验证页（BOSS 实际跳法）：续的是目标页而不是一次性验证页
    url = {"value": "https://www.zhipin.com/web/passport/zp/verify.html"
                    "?callbackUrl=https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fuser%2F&code=36"}
    goto: list[str] = []

    def _create(user_id):
        tab = object()
        m._browsers[user_id] = FakeChromium(tab)
        return tab

    async def _tab_url(tab):
        return url["value"]

    async def _goto(page, target, **kwargs):
        goto.append(target)
        return page

    monkeypatch.setattr(m, "_create_page", _create)
    monkeypatch.setattr(m, "tab_url", _tab_url)
    monkeypatch.setattr(m, "guarded_goto", _goto)
    return m, url, goto


async def _passed(user_id, timeout_s=0) -> bool:
    return True


async def test_resolve_risk_presents_the_page_then_waits(manager, monkeypatch):
    m, _, _ = manager
    await m.get_context("u1")
    presented = []

    monkeypatch.setattr(m, "present", lambda: presented.append(1) or True)
    monkeypatch.setattr(m, "wait_verify_cleared", _passed)
    assert await m.resolve_risk("u1") is True
    assert presented == [1]                            # 先交到人面前，再等他过验证


async def test_resolve_risk_keeps_the_pane_when_user_does_not_pass(manager, monkeypatch):
    """没过就不该把页面收走：人可能还在填。"""
    m, _, _ = manager
    await m.get_context("u1")

    async def fake_wait(user_id, timeout_s=0):
        return False

    monkeypatch.setattr(m, "wait_verify_cleared", fake_wait)
    assert await m.resolve_risk("u1") is False
    assert "u1" in m._browsers                      # 实例原样留着，由调用方决定


class FakeRiskBrowser:
    """风控人工介入要的浏览器接口：浏览器卡在风控页，直到人工介入才离开。

    tab_url 由 blocked 推导，而不是写死调用次序——调用方也会读 URL，
    按次序喂序列会把它多读的那一次算进去。
    """

    def __init__(self, passes: bool = True):
        self.passes = passes        # resolve_risk 是否等到人工过验证
        self.blocked = True         # 当前 tab 是否还停在安全验证页
        self.handoffs = 0
        self.released = 0

    async def get_context(self, uid):
        return object()

    async def tab_url(self, page):
        if self.blocked:
            return "https://www.zhipin.com/web/passport/zp/verify.html?code=36"
        return "https://www.zhipin.com/web/user/"

    async def guarded_goto(self, page, url, **kwargs):
        return page

    async def resolve_risk(self, uid, timeout_s=0):
        self.handoffs += 1
        self.blocked = not self.passes
        return self.passes

    async def release_context(self, uid):
        self.released += 1


def _search_action(browser, outcomes: list):
    """只测 _crawl_with_handoff 的走法：抓取本身打桩成脚本化的结果/异常。"""
    action = ActionSearchJobs(browser, match_service=None)

    async def fake_crawl_once(sid, cities, position):
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    action._crawl_once = fake_crawl_once
    return action


def _blocked() -> RiskBlocked:
    """撞上风控但没拿到码：按"还能人工过"那一档处理。"""
    return RiskBlocked()


async def test_search_handoff_only_triggers_on_risk_control():
    browser = FakeRiskBrowser(passes=True)
    action = _search_action(browser, [
        AppError(ErrorCode.JOB_SEARCH_FAILED, "没抓到岗位", http_status=502)])
    with pytest.raises(AppError) as e:
        await action._crawl_with_handoff("u1", ["郑州"], "python")
    assert e.value.error is ErrorCode.JOB_SEARCH_FAILED
    assert browser.handoffs == 0        # 抓不到岗位不该弹窗让人验证


async def test_search_handoff_searches_once_more_after_user_passes():
    browser = FakeRiskBrowser(passes=True)
    action = _search_action(browser, [_blocked(), [{"job_id": "j1"}]])
    assert await action._crawl_with_handoff("u1", ["郑州"], "python") == [{"job_id": "j1"}]
    assert browser.handoffs == 1
    assert browser.released == 0


async def test_search_still_blocked_after_handoff_returns_the_window():
    """过完验证仍被挡是限流：页面收干净，别留着让他以为再点一次就能过。"""
    browser = FakeRiskBrowser(passes=True)
    action = _search_action(browser, [_blocked(), _blocked()])
    with pytest.raises(AppError) as e:
        await action._crawl_with_handoff("u1", ["郑州"], "python")
    assert e.value.error is ErrorCode.PLATFORM_CAPTCHA
    assert STILL_BLOCKED_HINT in e.value.message
    assert browser.handoffs == 1        # 第二趟被挡不再弹第二次窗
    assert browser.released == 1


class FakeCrawlBrowser(FakeRiskBrowser):
    """够跑完整 _crawl_once 的最小接口：城市接口回什么、搜索包回什么由构造参数给。"""

    def __init__(self, city_body: dict, packet_body: dict | None):
        super().__init__(passes=True)
        self.blocked = False
        self.city_body = city_body
        self.packet_body = packet_body
        self.packets = 0

    async def fetch_json(self, page, url):
        return self.city_body

    async def listen_start(self, page, pattern):
        return None

    async def listen_wait(self, page, timeout_s):
        self.packets += 1
        return self.packet_body

    async def listen_stop(self, page):
        return None

    async def scroll_bottom(self, page):
        return None


CITY_OK = {"code": 0, "zpData": {"hotCitySites": [{"name": "杭州", "code": "101210100"}],
                                 "siteGroup": []}}


def _crawl_action(city_body: dict, packet_body: dict | None) -> tuple:
    browser = FakeCrawlBrowser(city_body, packet_body)
    return ActionSearchJobs(browser, match_service=None), browser


def test_blocked_url_recognises_the_ban_page():
    """403.html?code=32 以前认不出来，于是"号被封"被报成"没抓到岗位"。"""
    assert is_blocked_url(BANNED_URL)
    assert risk_code_of(BANNED_URL) == 32
    assert risk_code_of("https://www.zhipin.com/web/passport/zp/verify.html?code=36") == 36
    # 首页弹回不带 code：仍算挡住，但按"还能人工过验证"那一档处置
    bounce = "https://www.zhipin.com/?_security_check=1_1792249576163"
    assert is_blocked_url(bounce) and risk_code_of(bounce) is None
    # 过了校验的 _security_check 挂在正常 URL 上，不算挡住
    assert not is_blocked_url("https://www.zhipin.com/web/geek/jobs?query=py&_security_check=1_1")


def test_api_risk_reads_the_code_the_platform_reports():
    assert api_risk(BANNED_BODY) == (32, "您的账户存在异常行为，已暂时被禁止使用.")
    assert api_risk(CITY_OK) is None                    # code=0 是正常
    assert api_risk({"zpData": {"jobList": []}}) is None    # 没带 code 也不算风控
    assert api_risk(None) is None                       # 压根没等到包


async def test_banned_account_says_so_instead_of_popping_a_window():
    """城市接口先报 32：直接 5005 上去，别弹窗让人对着点不掉的东西白忙。"""
    action, browser = _crawl_action(BANNED_BODY, None)
    with pytest.raises(AppError) as e:
        await action._crawl_with_handoff("u1", ["杭州"], "python")
    assert e.value.error is ErrorCode.PLATFORM_ACCOUNT_BANNED
    assert "暂时限制了这个账号" in e.value.message
    assert "您的账户存在异常行为，已暂时被禁止使用." in e.value.message
    assert browser.handoffs == 0


async def test_banned_account_detected_from_the_joblist_packet_too():
    """搜索页不跳、只在 joblist.json 包里回码时，也不能算成"这个关键词没岗位"。"""
    action, browser = _crawl_action(CITY_OK, BANNED_BODY)
    with pytest.raises(AppError) as e:
        await action._crawl_with_handoff("u1", ["杭州"], "python")
    assert e.value.error is ErrorCode.PLATFORM_ACCOUNT_BANNED
    assert browser.packets == 1         # 第一包就收口，不再翻页
    assert browser.handoffs == 0


async def test_verify_code_in_packet_still_gets_the_window():
    """36 档还是老走法：报 5002，由 _crawl_with_handoff 弹窗给人过验证。"""
    action, browser = _crawl_action(CITY_OK, {"code": 36, "message": "您的账户存在异常行为.",
                                              "zpData": {}})
    with pytest.raises(AppError) as e:
        await action._crawl_with_handoff("u1", ["杭州"], "python")
    assert e.value.error is ErrorCode.PLATFORM_CAPTCHA
    assert browser.handoffs == 1

