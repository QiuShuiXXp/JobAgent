"""
职责：这台机器上 BOSS 页面长在哪块屏幕上。

两种启动形态的差别只有四件事：浏览器实例从哪来、不用了怎么收、怎么让人看见、
退出登录时 cookie 从哪儿清。四件事收在这里，infrastructure/browser.py 就只剩
「怎么读页面」，不再按形态到处分叉。

依赖方向是单向的：本模块 import browser.py 的异常与浏览器探测，
browser.py 只在类型标注里提到本模块（TYPE_CHECKING），运行时不 import。
"""
import asyncio
import logging
import socket
import zlib
from pathlib import Path
from typing import Awaitable, Callable

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import BrowserConnectError

from jobagent.config import paths
from jobagent.config.settings import settings
from jobagent.infrastructure.browser import (
    BrowserUnavailableError, browser_missing_hint, detect_browser_path,
)

logger = logging.getLogger("jobagent.viewport")

# 拿实例 / 收实例都是管理器的账，
# 清 cookie 的顺序却按形态不同，所以由管理器把这两个动作递进来。
Acquire = Callable[[], Awaitable[Chromium]]
Release = Callable[[], Awaitable[None]]


class BrowserViewport:
    """一块能给人看 BOSS 页面的「屏幕」：形态无关的四个动作。"""

    # 有没有一块可由用户折叠/展开的面板（只有桌面壳的内嵌左栏算）
    has_panel = False

    def acquire(self, user_id: str) -> Chromium:
        """拿到该用户的浏览器实例（同步，调用方放 to_thread 里跑）。"""
        raise NotImplementedError

    def dispose(self, browser: Chromium) -> None:
        """不再用这个实例时怎么收场（同步，调用方放 to_thread 里跑）。"""
        raise NotImplementedError

    def present(self) -> bool:
        """把 BOSS 页面摆到人面前；这个形态没有可摆的东西就返回 False。"""
        raise NotImplementedError

    def collapse(self) -> bool:
        """把页面从人眼前收起来（后台照常渲染）；没有可收的东西返回 False。"""
        return False

    def is_presented(self) -> bool:
        """页面现在在人眼里吗（登录/风控都靠它判断该等人还是该重来）。"""
        raise NotImplementedError

    async def drop_login_cookies(self, user_id: str,
                                 acquire: Acquire, release: Release) -> int:
        """退出登录：把该用户的登录态清干净，返回清掉的条数。"""
        raise NotImplementedError

    def describe(self) -> str:
        """启动日志用的一行自述：形态、端口、无头与否、用的哪个浏览器。"""
        raise NotImplementedError


class EmbeddedWebView2(BrowserViewport):
    """桌面形态：页面是壳窗口左栏里内嵌的一块 WebView2。

    进程、profile、窗口都是壳的，后端只从调试端口接管，所以
    「收场」不能 quit（左栏会变成崩溃页），只能导航回空白；
    「给人看」也不能自己弹窗，只能叫壳把左栏挪到前台。
    """

    def __init__(self, cdp_port: int, ctl_port: int | None = None) -> None:
        self.cdp_port = cdp_port
        self.ctl_port = ctl_port
        # 左栏现在展开着没有；登录/风控会展开，用户可经聊天页按钮收起
        self._presented = False

    @property
    def has_panel(self) -> bool:
        return bool(self.ctl_port)

    def acquire(self, user_id: str) -> Chromium:
        try:
            return Chromium(addr_or_opts=f"127.0.0.1:{self.cdp_port}")
        except BrowserConnectError as e:
            raise BrowserUnavailableError(
                f"连不上桌面壳内嵌浏览器的调试端口 {self.cdp_port}，"
                "通常是防火墙/安全软件拦截了本机回环连接：放行后重新打开应用") from e

    def dispose(self, browser: Chromium) -> None:
        browser.latest_tab.get("about:blank")

    def _ctl(self, command: str) -> bool:
        """给壳发一行指令，回 ok 才算成。"""
        if not self.ctl_port:
            return False
        try:
            with socket.create_connection(("127.0.0.1", self.ctl_port), timeout=3) as sock:
                sock.sendall(f"{command}\n".encode())
                return sock.recv(64).startswith(b"ok")
        except OSError as e:
            logger.warning("通知壳 %s 左栏失败（port=%s）：%s", command, self.ctl_port, e)
            return False

    def present(self) -> bool:
        """让壳把左栏展开。"""
        ok = self._ctl("reveal")
        self._presented = ok
        return ok

    def collapse(self) -> bool:
        """让壳把左栏挪出可视区：页面仍在后台渲染，登录态与翻页都不受影响。"""
        ok = self._ctl("collapse")
        if ok:
            self._presented = False
        return ok

    def is_presented(self) -> bool:
        return self._presented

    async def drop_login_cookies(self, user_id: str,
                                 acquire: Acquire, release: Release) -> int:
        """内嵌那块不能关进程，只能让浏览器自己清缓存里的 cookie。"""
        browser = await acquire()
        try:
            before = await asyncio.to_thread(browser.cookies)
            await asyncio.to_thread(browser.clear_cache, False, True)
        finally:
            await release()
        return len(before)

    def describe(self) -> str:
        return (f"内嵌 WebView2 127.0.0.1:{self.cdp_port}"
                f"（壳控制端口 {self.ctl_port or '未设置，左栏无法自动展开'}）")


class LocalChrome(BrowserViewport):
    """dev 形态：在普通浏览器里开 localhost，后端自己按用户拉一个 Chrome。

    这块屏幕就是那个 Chrome 窗口，所以默认带窗口（headless=False）：
    登录扫码、风控滑块都发生在人眼前，调试也看得见流程。
    profile 落在 storage/{user_id}/chrome_profile，登录态随目录持久化。
    """

    def __init__(self, headless: bool = False, base_port: int = 9333) -> None:
        self.headless = headless
        self.base_port = base_port

    def profile_dir(self, user_id: str) -> Path:
        """该用户的持久化 profile 目录：Cookie/登录痕迹全在这里。"""
        return Path(settings.storage_root) / user_id / "chrome_profile"

    def cookie_stores(self, user_id: str) -> list[Path]:
        """profile 里的 cookie 存储文件（含 journal/wal/shm 配套日志，留着会回放旧 cookie）。

        Chrome 把它们放在 <profile>/Default/Network/Cookies（本机实测，profile 目录名
        多一层 Default/Profile N）；老布局是 <profile>/Cookies。三种都要清，
        只清 <profile>/Network 会一个文件都碰不到，退出登录变成空操作。
        """
        profile = self.profile_dir(user_id)
        if not profile.exists():
            return []
        roots = [profile] + [p for p in profile.iterdir()
                             if p.is_dir() and (p.name == "Default"
                                               or p.name.startswith("Profile "))]
        out = []
        for root in roots:
            out.extend(root.glob("Cookies*"))
            out.extend((root / "Network").glob("Cookies*"))
        return out

    def acquire(self, user_id: str) -> Chromium:
        """为该用户起一个独立实例。

        隔离策略：
        - 调试端口：base_port + crc32(user_id) % 1000，避免多实例端口冲突
        - user_data 目录：storage/{user_id}/chrome_profile，Cookie/登录痕迹随此目录持久化
        """
        port = self.base_port + (zlib.crc32(user_id.encode("utf-8")) % 1000)
        user_dir = self.profile_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        browser_path = detect_browser_path()
        if browser_path is None:
            raise BrowserUnavailableError(browser_missing_hint())

        # 不覆写 UA：本机 Chrome 已是 154，伪造 Chrome/124 会和 sec-ch-ua 客户端提示对不上，
        # 实测直接被 BOSS 风控判 code=36「您的账户存在异常行为」并跳安全验证页
        opts = (ChromiumOptions().set_local_port(port).set_user_data_path(str(user_dir))
                .set_timeouts(base=10, page_load=10, script=10)
                .set_retry(interval=2, times=1)
                .set_load_mode("eager"))  # 不等待图片加载完成就返回
        opts.set_browser_path(browser_path)
        # 隐藏自动化标识（对齐 boss-skill-main config.py：绕过 BOSS直聘风控检测）
        opts.set_argument("--disable-blink-features=AutomationControlled")
        if self.headless:
            opts.headless(True)
        try:
            logger.info("启动浏览器 %s 端口=%s 无头=%s 用户目录=%s",
                        browser_path, port, self.headless, user_dir)
            return Chromium(addr_or_opts=opts)
        except BrowserConnectError as e:
            # 浏览器进程拉起来了但调试端口没通：常见于安全软件拦截 127.0.0.1 端口
            raise BrowserUnavailableError(
                f"浏览器（{Path(browser_path).name}）已启动但连不上调试端口 {port}，"
                "通常是防火墙/安全软件拦截：请放行本机回环连接后重试") from e

    def dispose(self, browser: Chromium) -> None:
        browser.quit()

    def present(self) -> bool:
        """窗口本来就摆在桌面上；无头形态没有窗口可给，顺便说清怎么改。"""
        if self.headless:
            logger.warning("浏览器在无头模式下运行，登录页/验证页都看不见："
                           "要盯着流程就把 BROWSER_HEADLESS=false 写进配置文件后重启后端")
        return not self.headless

    def is_presented(self) -> bool:
        return not self.headless

    async def drop_login_cookies(self, user_id: str,
                                 acquire: Acquire, release: Release) -> int:
        """直接删 cookie 库文件：退出时实例多半已经没了，而 Chrome 关掉后
        cookie 库就是唯一的登录态来源，靠 CDP 清反而清不到。

        必须先收实例再删文件，否则库还被进程锁着，删不掉。
        """
        await release()
        removed = 0
        for path in self.cookie_stores(user_id):
            try:
                path.unlink()
                removed += 1
            except OSError as e:          # Chrome 进程还没退干净：下次退出登录会再清
                logger.warning("cookie 文件 %s 仍被占用：%s", path, e)
        return removed

    def describe(self) -> str:
        return (f"本机 Chrome headless={self.headless} 端口基址={self.base_port} "
                f"exe={detect_browser_path() or '未找到'}")


def build_viewport() -> BrowserViewport:
    """按启动形态挑一块屏幕。

    全项目只有这一处做形态判断：壳传了调试端口就是内嵌 WebView2，
    否则是后端自己拉的本机 Chrome。别处一律只面向 BrowserViewport。
    """
    cdp_port = paths.env_port("JOBAGENT_CDP_PORT")
    if cdp_port:
        return EmbeddedWebView2(cdp_port, paths.env_port("JOBAGENT_SHELL_CTL_PORT"))
    return LocalChrome(headless=settings.browser_headless)
