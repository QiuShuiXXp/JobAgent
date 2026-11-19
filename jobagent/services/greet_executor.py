"""
greet_one：单岗位打招呼原子操作（TaskManager 逐岗位调用的最小执行单元）。

详情页 →「立即沟通」→ 关弹窗 →「继续沟通」→ JS 注入 #chat-input → 点发送。在它之上补两处它没有的东西：
1) 发送结果校验（boss-skill 点完发送直接记 applied）；
2) 风控/登录态识别，返回结构化结果供调度层决策（spec 5.5.1-8：只看页面，不伪造请求）。

dry_run（settings.greet_dry_run，默认 True）：导航 + 定位沟通入口就收手。
「立即沟通」点下去 BOSS 就会创建会话并带上系统默认招呼语，
所以演练模式必须整段跳过点击，而不是"点了但不发消息"。
"""
import asyncio
import json
import logging
from dataclasses import dataclass

from jobagent.config.settings import settings
from jobagent.infrastructure.browser import (
    BrowserManager, ElementMissingError, PageTimeout,
)
from jobagent.infrastructure.risk import is_blocked_url

logger = logging.getLogger("jobagent.action.greet")

DETAIL_URL = "https://www.zhipin.com/job_detail/{job_id}.html"
DETAIL_PATH = "/job_detail/"

# ---- 选择器（对齐 boss-skill auto_apply.py:36-56，补 DrissionPage 的 xpath: 前缀）----
# 立即沟通/继续沟通同一个 class，只有文案不同；class 用 contains 容错（页面上还有 btn-chat 变体）
START_CHAT_SELECTORS = (
    "xpath://a[contains(@class,'btn-startchat')][contains(.,'立即沟通')]",
    "xpath://div[@class='btn-container']//a[contains(.,'立即沟通')]",
    "css:.btn-startchat",
)
CONTINUE_CHAT_SELECTORS = (
    "xpath://a[contains(@class,'btn-startchat')][contains(.,'继续沟通')]",
    "xpath://div[@class='btn-container']//a[contains(.,'继续沟通')]",
)
DELIVER_RESUME_SELECTORS = (
    "xpath://div[@class='btn-container']//a[contains(.,'投递简历')]",
)
POPUP_CLOSE_SELECTORS = ("xpath://i[@class='icon-close']", "css:.icon-close", "css:.dialog-close")
CHAT_INPUT_SELECTORS = ("css:#chat-input", "css:div[contenteditable='true']",
                        'css:[placeholder*="消息"]', "css:textarea")
SEND_SELECTORS = ('css:button[type="send"]', "css:.btn-send", "css:.send-btn")

NAV_TIMEOUT_S = 30
SETTLE_S = 3.0          # 详情页是 SPA，guarded_goto 返回后按钮还没挂上（boss-skill 同值 sleep 3）
BTN_TIMEOUT_S = 8.0     # 沟通入口按钮异步挂载
ACTION_PAUSE_S = 2.0    # 点击后给页面反应时间
# 页面上出现这些文案代表平台侧拦截（实测 BOSS 的限流是以弹层文案 + verify 跳转两种形态出现）
RISK_TEXTS = ("访问过于频繁", "操作过于频繁", "请稍后再试", "安全验证",
              "验证码", "存在异常行为", "触发风控", "封禁", "已被限制", "账号异常")


@dataclass(slots=True)
class GreetOutcome:
    """status：SUCCEEDED/FAILED/RATE_LIMITED/LOGIN_INVALID/DRY_RUN。"""
    status: str
    failure_reason: str = ""
    message: str = ""   # 实际（或本应）发出的招呼语，落审计用


class GreetExecutor:
    """纯浏览器操作（不含调度/落库），被 TaskManager 与自测脚本共用。"""

    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser

    async def greet_one(self, page, job: dict, message: str,
                        dry_run: bool | None = None) -> GreetOutcome:
        dry_run = settings.greet_dry_run if dry_run is None else dry_run
        url = job.get("link") or DETAIL_URL.format(job_id=job["job_id"])
        try:
            await self.browser.guarded_goto(page, url, timeout_s=NAV_TIMEOUT_S)
        except (PageTimeout, ElementMissingError) as e:
            return GreetOutcome("FAILED", f"详情页加载失败: {e}", message)
        await asyncio.sleep(SETTLE_S)

        blocked = self._state_from_url(await self.browser.tab_url(page), url)
        if blocked is not None:
            return GreetOutcome(blocked, self._url_hint(blocked), message)

        # ---- 沟通入口：新岗位是「立即沟通」，已聊过是「继续沟通」----
        start = await self.browser.wait_displayed(page, START_CHAT_SELECTORS, BTN_TIMEOUT_S)
        if start is None:
            start = await self.browser.wait_displayed(page, DELIVER_RESUME_SELECTORS, 0.2)
        if start is None:
            already = await self.browser.wait_displayed(page, CONTINUE_CHAT_SELECTORS, 2.0)
            if already is not None:
                # 平台侧已存在会话：我们的 candidate_jobs 去重看不到它，只能在这里收住
                return GreetOutcome("FAILED", "该岗位已沟通过（页面显示继续沟通）", message)
            return GreetOutcome("FAILED", "详情页没有沟通/投递入口按钮", message)

        if dry_run:
            return GreetOutcome("DRY_RUN",
                                f"入口已定位（{await self._text_of(start)}），dry-run 未点击未发送",
                                message)

        # ---- 点击入口 → 关弹窗 → 继续沟通 ----
        await self.browser.click_element(start)
        await asyncio.sleep(ACTION_PAUSE_S)
        await self._close_popup(page)
        cont = await self.browser.wait_displayed(page, CONTINUE_CHAT_SELECTORS, 3.0)
        if cont is not None:
            await self.browser.click_element(cont)
            await asyncio.sleep(ACTION_PAUSE_S)
            await self._close_popup(page)

        # ---- 输入招呼语 ----
        if not await self._input_greeting(page, message):
            return GreetOutcome("FAILED", "招呼语未能输入聊天框（可能未进入会话页）", message)

        # ---- 发送 ----
        send = await self.browser.wait_displayed(page, SEND_SELECTORS, 3.0)
        if send is None:
            return GreetOutcome("FAILED", "招呼语已输入但发送按钮未找到", message)
        await self.browser.click_element(send)
        await asyncio.sleep(ACTION_PAUSE_S)

        body = await self._body_text(page)
        if message[-24:] in body:
            return GreetOutcome("SUCCEEDED", "", message)
        if self._hit_risk_text(body):
            return GreetOutcome("RATE_LIMITED", "页面出现平台限流/校验文案", message)
        return GreetOutcome("FAILED", "发送结果未确认（会话里没找到该消息）", message)

    # ================= 内部 =================
    @staticmethod
    def _state_from_url(landed: str, wanted: str) -> str | None:
        """导航落点判定：被风控弹走 / 掉登录 都必须在点按钮前收住。

        注意 _security_check 通过后也会挂在正常 URL 上（检索链路实测），
        所以"带着它却没落在目标页"才算被弹走。
        """
        if not landed:
            return "FAILED"
        # 风控落地页只有一份判定（infrastructure/risk.is_blocked_url）：36/32 两档在这儿都算限流，
        # 别在这儿再抄一遍字符串，否则搜索侧认出来的新形态这里会漏
        if is_blocked_url(landed):
            return "RATE_LIMITED"
        if "/web/user" in landed or "/web/geek/login" in landed:
            return "LOGIN_INVALID"
        if DETAIL_PATH not in landed and wanted.split("?")[0] not in landed:
            return "FAILED"
        return None

    @staticmethod
    def _url_hint(status: str) -> str:
        return {
            "RATE_LIMITED": "被BOSS直聘安全校验拦截",
            "LOGIN_INVALID": "登录态已失效，需重新登录",
            "FAILED": "导航未落在岗位详情页",
        }[status]

    @staticmethod
    async def _text_of(ele) -> str:
        """按钮文案（日志/dry-run 说明用）；取不到就回 unknown。"""
        try:
            return (await asyncio.to_thread(lambda: ele.text or "")).strip() or "unknown"
        except Exception:      # noqa: BLE001 元素可能在 SPA 重渲染中失效
            return "unknown"

    async def _close_popup(self, page) -> bool:
        """关掉点沟通后可能弹出的引导层（boss-skill _close_popup 同打法，失败算正常）。"""
        ele = await self.browser.wait_displayed(page, POPUP_CLOSE_SELECTORS, 0.5)
        if ele is None:
            return False
        try:
            await self.browser.click_element(ele)
        except Exception as e:      # noqa: BLE001 引导层抢不过就算了，不影响主流程
            logger.debug("关闭引导弹层失败：%r", e)
            return False
        return True

    async def _input_greeting(self, page, message: str) -> bool:
        """策略 A JS 注入（boss-skill 实测主力路径），失败退策略 B 元素输入。"""
        payload = json.dumps(message, ensure_ascii=False)
        script = f"""
            const el = document.querySelector(
                '#chat-input, [contenteditable="true"], textarea, [placeholder*="消息"]');
            if (!el) return 'not found';
            el.focus();
            if (el.isContentEditable) {{ el.innerText = {payload}; }}
            else {{ el.value = {payload}; }}
            ['input', 'change', 'compositionend'].forEach(
                t => el.dispatchEvent(new Event(t, {{ bubbles: true }})));
            return 'ok';
        """
        try:
            if "ok" in str(await self.browser.run_js(page, script)):
                await asyncio.sleep(0.5)
                return True
        except Exception as e:      # noqa: BLE001 JS 失败降级到元素输入
            logger.debug("打招呼脚本注入失败：%r", e)

        ele = await self.browser.wait_displayed(page, CHAT_INPUT_SELECTORS, 3.0)
        if ele is None:
            return False
        try:
            await self.browser.click_element(ele)
            await asyncio.to_thread(ele.input, message, clear=True)
        except Exception as e:      # noqa: BLE001
            logger.warning("打招呼输入框填写失败：%r", e)
            return False
        await asyncio.sleep(1.0)
        return True

    async def _body_text(self, page) -> str:
        try:
            return str(await self.browser.run_js(
                page, "return document.body.innerText.slice(0, 8000);"))
        except Exception as e:      # noqa: BLE001 读不到就当未确认
            logger.debug("读取页面正文失败：%r", e)
            return ""

    @staticmethod
    def _hit_risk_text(body: str) -> bool:
        return any(t in body for t in RISK_TEXTS)
