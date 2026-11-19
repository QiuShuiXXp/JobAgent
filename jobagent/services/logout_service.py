"""退出登录：把「这个 profile 已登录 BOSS」这件事彻底抹掉。

两处一起清才叫退出：浏览器 profile 的 cookie 存储、内存里的登录态槽位。
登录态只存在浏览器里（2026-10-18 起），DB 不再有凭证行。
意向/简历/候选岗/打招呼历史都不是登录记录，保持不动。
"""
import logging

from jobagent.infrastructure.browser import BrowserManager

logger = logging.getLogger("jobagent.logout")

# 登录态槽位：清掉后 job_list_confirm 门禁会重新锁上，逼出下一次登录。
# job_selection 也一起清：勾选是针对这次登录抓到的那批岗位，退出后 securityId 全废，
# 留着只会让 greet_task_flow 门禁开着、起一个必然失败打招呼任务。
LOGIN_SLOTS = ("login_status", "login_confirmed", "job_selection")
# 登录态流程：退出登录后从已完成集合移除，让依赖登录态的门禁重新锁上
LOGIN_FLOWS = ("boss_login", "job_list_confirm", "greet_task_flow")


class LogoutService:
    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser

    async def logout(self, user_id: str) -> dict:
        """清 cookie，返回清掉的数量（话术和日志都要用）。"""
        cookies = await self.browser.clear_login_cookies(user_id)
        logger.info("退出登录 %s：清除 cookie %s 个", user_id, cookies)
        return {"cookies_removed": cookies}
