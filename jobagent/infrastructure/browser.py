
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from DrissionPage import Chromium

from jobagent.config.settings import settings
from jobagent.infrastructure.risk import is_blocked_url, looks_like_captcha

if TYPE_CHECKING:
    from jobagent.infrastructure.viewport import BrowserViewport

logger = logging.getLogger("jobagent.browser")


class PageTimeout(Exception):
    """导航/等待超时（与验证码页区分）。"""


class BrowserUnavailableError(Exception):
    """本机起不来可用浏览器（没装 Chrome/Edge、路径配错、调试端口被拦）。"""


class CaptchaPageError(Exception):
    """识别到验证码/风控页（打招呼任务据此停止剩余岗位）。"""


class ElementMissingError(Exception):
    """目标元素未找到（页面结构变更的第一信号）。"""


# ---- 风控人工介入的等待参数（判定与话术在 infrastructure/risk.py）----
# 人在页面里滑块/输验证码的时间：太短等于没给，太长前端这一轮就挂太久
VERIFY_WAIT_TIMEOUT_S = 180
VERIFY_POLL_S = 2


# BOSS直聘登录态 auth cookie（真实登录 profile 实测）：任一存在即已登录。
# profile 级信号：不依赖授权跳转落在哪个 tab、也不受 tab 对象生死影响，比页面元素判定可靠。
AUTH_COOKIE_NAMES = ("wt2", "bst", "zp_at")

# DrissionPage 4.1.1.4 只会去找 chrome.exe（注册表 App Paths\chrome.exe + PATH），

# FileNotFoundError，被全局兜底成「错误 1005：内部错误」——看不出是没浏览器。
# 这里自己按 Chrome → Edge 找一遍，找不到就抛能读的错。
_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{}"
_WELL_KNOWN = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _from_registry(exe_name: str) -> str | None:
    """读注册表 App Paths 里登记的浏览器绝对路径（安装器写的，比猜目录可靠）。"""
    try:
        from winreg import (KEY_READ, HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER,
                            CloseKey, EnumValue, OpenKey)
    except ImportError:                      # 非 Windows
        return None
    for hive in (HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE):
        try:
            key = OpenKey(hive, _APP_PATHS.format(exe_name), 0, KEY_READ)
        except OSError:
            continue
        try:
            value = EnumValue(key, 0)[1]
        except OSError:
            value = ""
        finally:
            CloseKey(key)
        if value and Path(value).is_file():
            return str(value)
    return None


def detect_browser_path() -> str | None:
    """本机可用的 Chromium 浏览器 exe 绝对路径；一个都没有返回 None。

    只有 dev 形态（在普通浏览器里开 localhost，后端自己拉一个 Chrome）会走到这里 ——
    桌面形态的 BOSS 页面是壳内嵌的 WebView2，不需要本机浏览器。
    顺序：配置文件 BROWSER_PATH（便携版/国产套壳）> 仓库里 fetch_browser.py 拉的 chrome-win64 >
    系统 Chrome > 系统 Edge。
    """
    from jobagent.config.paths import bundled_browser
    from shutil import which
    configured = (settings.browser_path or "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    bundled = bundled_browser()
    if bundled.is_file():
        return str(bundled)
    for exe in ("chrome.exe", "msedge.exe"):
        path = _from_registry(exe) or which(exe[:-4])
        if path and Path(path).is_file():
            return str(path)
    return next((p for p in _WELL_KNOWN if Path(p).is_file()), None)


def browser_missing_hint() -> str:
    """dev 形态找不到本机浏览器时的可操作文案（桌面形态不会走到这里）。"""
    from jobagent.config.paths import config_file
    configured = (settings.browser_path or "").strip()
    if configured:
        return (f"BROWSER_PATH 指向的浏览器不存在：{configured}\n"
                f"改成 chrome.exe / msedge.exe 的完整路径，或删掉这行让它自动找。")
    return (f"这台电脑上没有可用的 Chrome 或 Edge，dev 形态的后端只能自己拉浏览器，"
            f"所以打不开 BOSS直聘。装其中一个，或在 {config_file()} 里加一行 "
            f"BROWSER_PATH=<浏览器 exe 的完整路径> 后重新打开应用。")


class BrowserManager:

    def __init__(self, viewport: "BrowserViewport") -> None:
        """初始化浏览器管理器。
        Args:
            viewport: 页面长在哪块屏幕上（壳内嵌 WebView2 / 本机 Chrome），
                      形态差异全在它身上，这里只调它的四个动作
        """
        self.viewport = viewport
        # 存储 Chromium 对象
        self._browsers: dict[str, Chromium] = {}

    # ---- 实例管理：每用户独立 Chromium（调试端口 + user_data 双隔离）----
    async def get_context(self, user_id: str):
        """
        获取（或创建）该用户的浏览器 Tab 对象。
        """
        if user_id not in self._browsers:
            await asyncio.to_thread(self._create_page, user_id)
        return self._browsers[user_id].latest_tab

    def _create_page(self, user_id: str):
        """向视口要一个实例并记进账（同步方法，在 to_thread 中执行）。"""
        browser = self.viewport.acquire(user_id)
        self._browsers[user_id] = browser
        return browser.latest_tab

    def has_context(self, user_id: str) -> bool:
        """该用户是否已有浏览器实例。"""
        return user_id in self._browsers

    async def _ensure_browser(self, user_id: str) -> Chromium:
        """确保实例存在并拿到它本身（清 cookie 要直接对浏览器说话，不是对 tab）。"""
        await self.get_context(user_id)
        return self._browsers[user_id]

    async def release_context(self, user_id: str) -> None:
        """不再使用该用户的浏览器：怎么收场由形态决定（关进程 / 只回空白页）。"""
        browser = self._browsers.pop(user_id, None)
        if browser is None:
            return
        await asyncio.to_thread(self.viewport.dispose, browser)

    async def stop(self) -> None:
        """关闭所有浏览器实例（应用退出时调用）。"""
        for uid in list(self._browsers):
            await self.release_context(uid)

    async def clear_login_cookies(self, user_id: str) -> int:
        """退出登录：把这个用户的登录态清干净，保证下次必须重新登录。返回清掉的条数。

        清法和收实例的顺序按形态不同（见 viewport），这里只把两个动作递过去。
        """
        return await self.viewport.drop_login_cookies(
            user_id,
            acquire=lambda: self._ensure_browser(user_id),
            release=lambda: self.release_context(user_id))

    # ---- 把 BOSS 页面交给人：摆到眼前 / 等人工过验证 ----

    def is_presented(self) -> bool:
        """BOSS 页面现在在人眼里吗：内嵌形态看左栏展开过没有，本机 Chrome 看有没有窗口。"""
        return self.viewport.is_presented()

    def present(self) -> bool:
        """把 BOSS 页面摆到人面前；这块形态没得摆就 False。"""
        return self.viewport.present()

    def collapse(self) -> bool:
        """把 BOSS 页面从人眼前收起（后台照常跑）；这块形态没得收就 False。"""
        return self.viewport.collapse()

    async def on_verify_page(self, user_id: str) -> bool:
        """该用户当前页面是不是 BOSS 风控落地页。"""
        browser = self._browsers.get(user_id)
        if browser is None:
            return False
        try:
            return is_blocked_url(await self.tab_url(browser.latest_tab))
        except Exception:
            return False

    async def wait_verify_cleared(self, user_id: str,
                                  timeout_s: float = VERIFY_WAIT_TIMEOUT_S) -> bool:
        """等人在页面里把安全验证过掉：验证通过会自动跳回 callback，所以看 URL 就够。"""
        end = time.perf_counter() + timeout_s
        while time.perf_counter() < end:
            if self._browsers.get(user_id) is None:
                return False        # 实例被回收或用户自己关了窗
            if not await self.on_verify_page(user_id):
                return True
            await asyncio.sleep(VERIFY_POLL_S)
        return False

    async def resolve_risk(self, user_id: str,
                           timeout_s: float = VERIFY_WAIT_TIMEOUT_S) -> bool:
        """风控挡人时的一整轮：把 BOSS 页面展开到人面前，等他亲手把验证过掉。

        过不了也不收回——人可能还在填；调用方拿到 False 后自己决定是 release 还是留着等下一轮。
        """
        self.present()
        return await self.wait_verify_cleared(user_id, timeout_s)


    async def guarded_goto(self, tab, url: str,
                               wait_selector: str | None = None,
                               timeout_s: float = 30.0):
            try:

                ok = await asyncio.to_thread(
                    tab.get, url, show_errmsg=True, retry=1, interval=2, timeout=timeout_s)
                if not ok:  # 双保险：异常路径漏网时兜住（返回 False 但未抛异常）
                    raise PageTimeout(f"goto {url} failed")
            except PageTimeout:
                raise
            except Exception as e:

                raise PageTimeout(f"goto {url} timeout") from e

            if wait_selector is not None:
                # wait.ele_displayed：等待元素可见，超时返回 False
                found = await asyncio.to_thread(
                    tab.wait.ele_displayed, wait_selector, timeout=10)
                if not found:
                    # 元素未出现：先检查是否是验证码页（风控信号）
                    if looks_like_captcha(await self.html(tab)):
                        raise CaptchaPageError(f"captcha page at {url}")
                    # 不是验证码页 → 页面结构变更或选择器错误
                    raise ElementMissingError(f"selector {wait_selector} missing at {url}")
            return tab



    @staticmethod
    async def click_element(ele) -> None:

        try:
            await asyncio.to_thread(lambda: ele.click())
        except Exception:
            await asyncio.to_thread(lambda: ele.click(by_js=True))

    async def find_displayed(self, tab, sel: str):
        """找单个可见元素，找不到返回 None。

        """
        ele = await asyncio.to_thread(tab.ele, sel, 1, 0.2)
        if not ele:
            return None
        try:
            return ele if ele.states.is_displayed else None
        except Exception:
            return None

    async def wait_displayed(self, tab, candidates, timeout: float):
        """轮询等第一个可见元素出现。

        登录页是前端渲染的：guarded_goto 返回时 DOM 里可能还没有这些节点，
        只查一次会误判"页面结构变更"，必须轮询到超时。
        """
        end = time.perf_counter() + timeout
        while True:
            for sel in candidates:
                ele = await self.find_displayed(tab, sel)
                if ele is not None:
                    return ele
            if time.perf_counter() >= end:
                return None
            await asyncio.sleep(0.3)

    async def html(self, tab) -> str:
            """获取当前页面的 HTML 源码。"""
            return await asyncio.to_thread(lambda: tab.html)

    async def tab_url(self, tab) -> str:
            """获取当前页面 URL。"""
            return await asyncio.to_thread(lambda: tab.url or "")

    async def run_js(self, tab, script: str):
            """执行 JS 并返回结果（打招呼文案注入、发送结果校验都要读页面运行时状态）。"""
            return await asyncio.to_thread(tab.run_js, script)

    async def cookies(self, tab) -> list[dict]:
            """获取当前页面的 Cookie 列表（用于登录态检查/持久化）。"""
            return await asyncio.to_thread(tab.cookies)

    # ---- 抓包/滚动：岗位列表走 XHR 翻页，监听接口
    async def listen_start(self, tab, target: str) -> None:
        """开始监听匹配的 XHR/fetch 包。"""
        await asyncio.to_thread(tab.listen.start, target)

    async def listen_wait(self, tab, timeout: float) -> dict | None:
        """
        等一个监听包，返回响应 body（dict）；超时/异常返回 None。
        """
        packet = await asyncio.to_thread(lambda: tab.listen.wait(timeout=timeout))
        if not packet or isinstance(packet, bool):
            return None
        try:
            return packet.response.body
        except Exception:
            return None

    async def listen_stop(self, tab) -> None:
        await asyncio.to_thread(tab.listen.stop)

    async def scroll_bottom(self, tab) -> None:
        """滚到底：BOSS 搜索页滚到底才触发下一页 joblist.json。"""
        await asyncio.to_thread(tab.scroll.to_bottom)

    async def fetch_json(self, tab, url: str) -> dict | None:
        """导航到 JSON 接口并解析（BOSS wapi 返回裸 JSON，浏览器会包一层 <pre>）。"""
        await self.guarded_goto(tab, url, timeout_s=20)
        html = await self.html(tab)
        m = re.search(r"<pre[^>]*>(.*?)</pre>", html, re.DOTALL)
        raw = m.group(1) if m else html
        try:
            return json.loads(raw)
        except Exception:
            logger.warning("fetch_json 解析失败：%s", url)
            return None

    async def all_cookies(self, user_id: str) -> list[dict]:
        """浏览器级 cookie（不依赖 tab 导航）：新标签页上也能读到全站 cookie。"""
        browser = self._browsers.get(user_id)
        if browser is None:
            return []
        return await asyncio.to_thread(browser.cookies)

    async def logged_in(self, user_id: str) -> bool:
        """该用户浏览器 profile 是否已带登录态（auth cookie 判定，无需导航）。"""
        names = {c.get("name") for c in await self.all_cookies(user_id)}
        return bool(names & set(AUTH_COOKIE_NAMES))
