"""两种启动形态的视口单测：不起 Chrome、不连壳，只验四个动作的走法。

形态差异全在 viewport 里，所以这里直接测视口；browser.py 那边只测它有没有把活派下去。
"""
import pytest

from jobagent.config.settings import settings
from jobagent.infrastructure import viewport as viewport_mod
from jobagent.infrastructure.browser import BrowserManager, BrowserUnavailableError
from jobagent.infrastructure.viewport import (
    EmbeddedWebView2, LocalChrome, build_viewport,
)


def _fake_shell_socket(sent, reply=b"ok\n"):
    """顶替 socket.create_connection 返回的那个连接。"""

    class FakeSock:
        def sendall(self, data):
            sent.append(data)

        def recv(self, _n):
            return reply

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda *a, **k: FakeSock()


# ---- 摆到人眼前 ----

def test_embedded_present_asks_the_shell_to_open_the_pane(monkeypatch):
    sent: list[bytes] = []
    monkeypatch.setattr(viewport_mod.socket, "create_connection", _fake_shell_socket(sent))
    v = EmbeddedWebView2(cdp_port=9712, ctl_port=9713)
    assert v.present() is True
    assert sent == [b"reveal\n"]
    assert v.is_presented() is True                 # 展开之后就算在人眼里


def test_embedded_present_without_a_shell_port_is_a_no_op():
    v = EmbeddedWebView2(cdp_port=9712)             # 壳没给控制端口：叫不动它
    assert v.present() is False
    assert v.is_presented() is False


def test_embedded_present_reports_failure_when_the_shell_says_no(monkeypatch):
    monkeypatch.setattr(viewport_mod.socket, "create_connection",
                        _fake_shell_socket([], reply=b"err\n"))
    v = EmbeddedWebView2(cdp_port=9712, ctl_port=9713)
    assert v.present() is False
    assert v.is_presented() is False


# ---- 收起（用户经聊天页按钮折叠左栏）----

def test_embedded_collapse_hides_the_pane_and_can_reveal_again(monkeypatch):
    sent: list[bytes] = []
    monkeypatch.setattr(viewport_mod.socket, "create_connection", _fake_shell_socket(sent))
    v = EmbeddedWebView2(cdp_port=9712, ctl_port=9713)
    v.present()
    assert v.collapse() is True
    assert sent == [b"reveal\n", b"collapse\n"]
    assert v.is_presented() is False                # 收起了：登录确认会按"页面不在眼前"走
    assert v.present() is True                      # 再点展开还能回来
    assert v.is_presented() is True


def test_embedded_collapse_without_a_shell_port_is_a_no_op():
    v = EmbeddedWebView2(cdp_port=9712)
    assert v.collapse() is False


def test_embedded_collapse_failure_keeps_the_presented_state(monkeypatch):
    monkeypatch.setattr(viewport_mod.socket, "create_connection",
                        _fake_shell_socket([], reply=b"err\n"))
    v = EmbeddedWebView2(cdp_port=9712, ctl_port=9713)
    v._presented = True
    assert v.collapse() is False
    assert v.is_presented() is True                 # 壳没答应就不能谎报已收起


def test_local_chrome_has_nothing_to_collapse():
    v = LocalChrome()
    assert v.collapse() is False
    assert v.has_panel is False


def test_has_panel_needs_the_shell_control_port():
    assert EmbeddedWebView2(cdp_port=9712).has_panel is False
    assert EmbeddedWebView2(cdp_port=9712, ctl_port=9713).has_panel is True


def test_local_chrome_is_presented_only_when_it_has_a_window():
    """dev 形态要 debug 看流程，默认就得带窗口；无头时明确说没有可摆的东西。"""
    headed = LocalChrome(headless=False)
    assert headed.present() is True and headed.is_presented() is True
    headless = LocalChrome(headless=True)
    assert headless.present() is False and headless.is_presented() is False


# ---- 收场 ----

def test_embedded_dispose_blanks_the_pane_instead_of_quitting():
    """内嵌那块是壳窗口的一部分，quit 会把左栏变成崩溃页——只能导航回空白。"""
    events: list[str] = []

    class FakeTab:
        def get(self, url):
            events.append(url)

    class FakeBrowser:
        latest_tab = FakeTab()

        def quit(self):
            events.append("QUIT")

    EmbeddedWebView2(cdp_port=9712).dispose(FakeBrowser())
    assert events == ["about:blank"]


def test_local_chrome_dispose_quits_the_process():
    events: list[str] = []

    class FakeBrowser:
        def quit(self):
            events.append("QUIT")

    LocalChrome().dispose(FakeBrowser())
    assert events == ["QUIT"]


async def test_manager_routes_release_to_the_viewport():
    events: list[str] = []

    class FakeBrowser:
        def quit(self):
            events.append("QUIT")

    m = BrowserManager(LocalChrome(headless=True))
    m._browsers["u1"] = FakeBrowser()
    await m.release_context("u1")
    assert events == ["QUIT"]
    assert m._browsers == {} and not m.has_context("u1")


# ---- 拿实例 ----

def test_embedded_acquire_turns_a_dead_port_into_a_readable_error(monkeypatch):
    from DrissionPage.errors import BrowserConnectError

    def boom(*a, **k):
        raise BrowserConnectError("connect failed")

    monkeypatch.setattr(viewport_mod, "Chromium", boom)
    with pytest.raises(BrowserUnavailableError) as e:
        EmbeddedWebView2(cdp_port=9712).acquire("u1")
    assert "9712" in str(e.value) and "防火墙" in str(e.value)


def test_local_acquire_without_any_browser_says_how_to_fix_it(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    monkeypatch.setattr(viewport_mod, "detect_browser_path", lambda: None)
    with pytest.raises(BrowserUnavailableError) as e:
        LocalChrome().acquire("u1")
    assert "BROWSER_PATH" in str(e.value)           # 文案要能照着做


# ---- 退出登录清 cookie ----

class FakeEmbeddedBrowser:
    def __init__(self, cookies, raises: bool = False):
        self._cookies = cookies
        self.raises = raises
        self.events: list[str] = []

    def cookies(self):
        self.events.append("cookies")
        return self._cookies

    def clear_cache(self, *args):
        self.events.append("clear_cache")
        if self.raises:
            raise RuntimeError("cdp gone")


async def test_embedded_logout_lets_the_browser_clear_its_own_cache():
    """内嵌那块不能关进程，只能叫浏览器自己清；清完仍要把页面带回空白。"""
    browser = FakeEmbeddedBrowser([{"name": "wt2"}, {"name": "bst"}])
    released: list[str] = []

    async def acquire():
        return browser

    async def release():
        released.append("release")
        browser.events.append("release")

    removed = await EmbeddedWebView2(cdp_port=9712).drop_login_cookies(
        "u1", acquire, release)
    assert removed == 2
    assert browser.events == ["cookies", "clear_cache", "release"]


async def test_embedded_logout_still_releases_the_instance_when_clearing_fails():
    browser = FakeEmbeddedBrowser([], raises=True)

    async def acquire():
        return browser

    async def release():
        browser.events.append("release")

    with pytest.raises(RuntimeError):
        await EmbeddedWebView2(cdp_port=9712).drop_login_cookies("u1", acquire, release)
    assert browser.events == ["cookies", "clear_cache", "release"]   # 实例必须收掉


async def test_local_logout_quits_before_deleting_the_cookie_files(tmp_path, monkeypatch):
    """顺序是硬约束：cookie 库是 Chrome 退出时才落盘的，先删等于没删。

    这里让 release 把库文件"写出来"，模拟进程退出时的那次落盘；
    删早了就会一个文件都碰不到，退出登录变成空操作。
    """
    v = LocalChrome(headless=True)
    monkeypatch.setattr(v, "profile_dir", lambda uid: tmp_path)

    async def acquire():
        raise AssertionError("dev 形态退出不该再起实例")

    async def release():
        for rel in ("Cookies", "Default/Network/Cookies", "Default/Network/Cookies-journal"):
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

    assert await v.drop_login_cookies("u1", acquire, release) == 3
    assert list(tmp_path.rglob("Cookies*")) == []


def test_local_cookie_stores_covers_all_three_layouts(tmp_path, monkeypatch):
    """老布局 <profile>/Cookies、新布局 <profile>/Default/Network/Cookies 都要认。"""
    v = LocalChrome()
    monkeypatch.setattr(v, "profile_dir", lambda uid: tmp_path)
    for rel in ("Cookies", "Default/Network/Cookies", "Default/Network/Cookies-wal",
                "Profile 1/Network/Cookies", "Default/Cookies"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    (tmp_path / "Local State").write_bytes(b"x")     # 不是 cookie 库，别碰
    assert len(v.cookie_stores("u1")) == 5


# ---- 形态判定只有一处 ----

def test_build_viewport_follows_the_shell_ports(monkeypatch):
    monkeypatch.setenv("JOBAGENT_CDP_PORT", "9712")
    monkeypatch.setenv("JOBAGENT_SHELL_CTL_PORT", "9713")
    v = build_viewport()
    assert isinstance(v, EmbeddedWebView2)
    assert (v.cdp_port, v.ctl_port) == (9712, 9713)


def test_build_viewport_falls_back_to_local_chrome(monkeypatch):
    monkeypatch.delenv("JOBAGENT_CDP_PORT", raising=False)
    monkeypatch.delenv("JOBAGENT_SHELL_CTL_PORT", raising=False)
    v = build_viewport()
    assert isinstance(v, LocalChrome)
    assert v.headless is settings.browser_headless


def test_build_viewport_ignores_a_port_it_cannot_parse(monkeypatch):
    """壳写坏了环境变量也不能去拉本机 Chrome 顶上：宁可按 dev 形态走。"""
    monkeypatch.setenv("JOBAGENT_CDP_PORT", "not-a-port")
    assert isinstance(build_viewport(), LocalChrome)


def test_dev_form_defaults_to_a_window_he_can_watch():
    """网页端要 debug 看流程：默认必须是有窗口的。"""
    assert settings.browser_headless is False


def test_describe_names_the_form_for_the_startup_log(monkeypatch):
    monkeypatch.setattr(viewport_mod, "detect_browser_path", lambda: r"C:\x\chrome.exe")
    assert "9712" in EmbeddedWebView2(cdp_port=9712, ctl_port=9713).describe()
    local = LocalChrome(headless=True).describe()
    assert "headless=True" in local and "chrome.exe" in local
