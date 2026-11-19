"""岗位检索动作：抓取（字段容错）→ 匹配 → candidate_jobs 落库 → job_list 卡片。

run() 只做四段编排，每段一个私有方法：
  _crawl_jobs     抓取（翻页抓 → 空结果收口）
  _rank_jobs      筛掉看过的 → 规则匹配打分
  _save_candidates 候选岗落库
  _build_result   岗位卡片 + 汇总话术
"""
import asyncio
import json
import logging
import urllib.parse
from pathlib import Path

from jobagent.api.errors import AppError, ErrorCode
from jobagent.config.settings import settings
from jobagent.domain.messages import BotMessage, FocusedObject, MessageType
from jobagent.domain.state import DialogueState
from jobagent.infrastructure.browser import BrowserManager
from jobagent.infrastructure.db import get_session
from jobagent.infrastructure.risk import (
    BANNED_HINT, STILL_BLOCKED_HINT, VERIFY_TIMEOUT_HINT, RiskBlocked, api_risk,
    is_blocked_url, risk_code_of,
)
from jobagent.repository.business_repositories import CandidateJobRepository
from jobagent.services.match_service import MatchService, RankedJob
from jobagent.task.action.base import Action, ActionResult

logger = logging.getLogger("jobagent.action.search_jobs")

BOSS_BASE_URL = "https://www.zhipin.com"
JOBLIST_API = "zpgeek/search/joblist.json"
CITY_SITE_URL = f"{BOSS_BASE_URL}/wapi/zpgeek/common/data/city/site.json"
SEARCH_PATH = "/web/geek/jobs"
MAX_PAGES = 3
LISTEN_TIMEOUT_S = 5
PAGE_INTERVAL_S = 2


# ============ 纯映射/话术工具（不碰浏览器与数据库）============

def _abort_on_api_risk(body: dict | None) -> None:
    """接口包自己回码时立刻收口（joblist.json 与 city/site.json 两条路同一种认法）。"""
    risk = api_risk(body)
    if risk:
        raise RiskBlocked(*risk)


def _normalize_job(raw: dict) -> dict | None:
    """BOSS 岗位 JSON → 内部字段。encryptJobId 是唯一的岗位主键，没有就丢弃。"""
    job_id = raw.get("encryptJobId") or ""
    if not job_id:
        return None
    return {
        "job_id": job_id,
        "title": raw.get("jobName") or "",
        "city": raw.get("cityName") or "",
        "district": raw.get("areaDistrict") or "",
        "company": raw.get("brandName") or "",
        "salary_text": raw.get("salaryDesc") or "",
        "experience": raw.get("jobExperience") or "",
        "degree": raw.get("jobDegree") or "",
        "skills": raw.get("skills") or [],
        "welfare": raw.get("welfareList") or [],
        "link": f"{BOSS_BASE_URL}/job_detail/{job_id}.html",
    }


def _db_rows(ranked: list[RankedJob]) -> list[dict]:
    """落库投影：键名必须等于 candidate_jobs 的列名 —— upsert_candidates 是 **row 建记录。"""
    return [{"job_id": r.job["job_id"], "title": r.job["title"], "company": r.job["company"],
             "salary_text": r.job["salary_text"], "city": r.job["city"],
             "match_score": r.result.score, "score_reasons": r.result.reasons} for r in ranked]


def _card_rows(ranked: list[RankedJob]) -> list[dict]:
    """卡片投影：前端读 id/salary/score，和库里的列名不同名，所以不能复用 _db_rows。"""
    return [{"id": r.job["job_id"], "title": r.job["title"], "company": r.job["company"],
             "salary": r.job["salary_text"], "city": r.job["city"],
             "score": round(r.result.score, 2)} for r in ranked]


def _summary(total: int, pooled: int, kept: int, *, recycled: bool, below_min: bool) -> str:
    if recycled:
        text = (f"抓到 {total} 个岗位，全是你看过的——旧的重新按匹配度排了 {kept} 个。"
                "想真换一批，改改岗位关键词或放宽城市。")
    else:
        text = f"抓到 {total} 个岗位，跳过 {total - pooled} 个看过的，按简历匹配出 {kept} 个新候选。"
    if below_min:
        text += "匹配数偏少，可考虑放宽薪资或城市要求。"
    return text + "请在卡片中勾选想投递的岗位。"


class ActionSearchJobs(Action):
    """岗位检索：抓取 → 匹配 → 落库 → 卡片，四段编排都在 run()，细节收敛到私有方法。"""

    name = "action_search_jobs"

    def __init__(self, browser: BrowserManager, match_service: MatchService) -> None:
        self.browser = browser
        self.match_service = match_service
        self._city_codes: dict[str, str] = {}

    # ---------- run：四段编排 ----------
    async def run(self, state: DialogueState, action_args: dict) -> ActionResult:
        # 获取意向城市、岗位
        position, cities = self._gates(state)
        # 抓取岗位
        jobs = await self._crawl_jobs(state, position, cities)
        # 打分
        ranked, pooled, recycled, below_min = await self._rank_jobs(state, jobs)
        await self._save_candidates(state.sender_id, ranked)
        return self._build_result(jobs, ranked, pooled, recycled, below_min)

    async def _crawl_jobs(self, state: DialogueState, position: str,
                          cities: list[str]) -> list[dict]:
        """抓取段：空结果直接收口，不进匹配。"""
        sid = state.sender_id
        jobs = await self._crawl_with_handoff(sid, cities, position)
        if not jobs:
            raise AppError(ErrorCode.JOB_SEARCH_FAILED,
                           "没抓到岗位，换个岗位关键词或城市再试", http_status=502)
        logger.info("为用户 %s 抓到 %d 个岗位（方向=%s，城市=%s）",
                    sid, len(jobs), position, cities)
        return jobs

    async def _rank_jobs(self, state: DialogueState, jobs: list[dict]) -> tuple:
        """匹配段：筛掉看过的 → 规则打分 → 返回 (ranked, pooled, recycled, below_min)。"""
        sid = state.sender_id
        async with get_session() as session:
            repo = CandidateJobRepository(session)
            greeted = await repo.greeted_job_ids(sid)
            known = await repo.known_job_ids(sid)
        # 先筛再打分：规则匹配是按岗位逐个调的，看过的没必要再花一次
        fresh = [j for j in jobs if j["job_id"] not in known]
        pool = fresh or jobs                     # 全刷完了才回炉旧池，别让卡片空着

        ranked, below_min = await self.match_service.match(
            self._resume_dict(state), pool, greeted)
        self.match_service.assert_non_empty(ranked)
        return ranked, len(pool), not fresh, below_min

    async def _save_candidates(self, sid: str, ranked: list[RankedJob]) -> None:
        """落库段：候选岗写入 candidate_jobs（GREETED/SELECTED 终态由仓库层保护）。"""
        async with get_session() as session:
            await CandidateJobRepository(session).upsert_candidates(sid, _db_rows(ranked))

    def _build_result(self, jobs: list[dict], ranked: list[RankedJob],
                      pooled: int, recycled: bool, below_min: bool) -> ActionResult:
        """卡片段：岗位列表卡片 + 汇总话术。"""
        card = BotMessage(type=MessageType.OBJECT, object=FocusedObject(
            type="job_list", id="candidates", payload={"jobs": _card_rows(ranked)}))
        summary = _summary(len(jobs), pooled, len(ranked), recycled=recycled, below_min=below_min)
        return ActionResult(messages=[card, BotMessage(text=summary)])

    # ---------- 门禁 ----------
    def _gates(self, state: DialogueState) -> tuple[str, list[str]]:
        """执行层门禁：流程的条件边已经挡过一轮，这里再兜一次（规划器可能拿过期槽位直调）。"""
        self.require_login(state)
        if state.get_slots("resume_confirmed") != "确认":
            raise AppError(ErrorCode.RESUME_NOT_CONFIRMED,
                           "请先上传并确认简历，再来筛岗位", http_status=403)
        position = str(state.get_slots("intent_position") or "").strip()
        if not position:
            raise AppError(ErrorCode.INTENT_NOT_CONFIRMED,
                           "还没有岗位方向，请先完成求职意向确认", http_status=403)
        cities = [c.strip() for c in str(state.get_slots("intent_cities") or "").split(",")
                  if c.strip()]
        return position, cities

    # ---------- 抓取 ----------
    async def _crawl_with_handoff(self, sid: str, cities: list[str],
                                  position: str) -> list[dict]:
        """抓岗位；被风控挡住就把验证页交到人面前，他过了再抓一轮。

        只让人介入一次：第二轮还被挡就是限流，报 5002 收手；
        账号级禁用（32）不介入，直接报 5005。
        """
        try:
            return await self._crawl_once(sid, cities, position)
        except RiskBlocked as e:
            if not e.human_passable:
                raise AppError(ErrorCode.PLATFORM_ACCOUNT_BANNED,
                               f"{BANNED_HINT}{e.detail}", http_status=403) from e
            logger.warning("%s 的检索被风控拦住，把页面交给用户处理", sid)
        if not await self.browser.resolve_risk(sid):
            raise AppError(ErrorCode.PLATFORM_CAPTCHA, VERIFY_TIMEOUT_HINT, http_status=403)
        logger.info("用户完成了 %s 的验证页，再检索一次", sid)
        try:
            return await self._crawl_once(sid, cities, position)
        except RiskBlocked as e:
            # 过完验证还被挡 = 限流，不是缺一次点击：页面留着只会让他以为还能再试
            await self.browser.release_context(sid)
            raise AppError(ErrorCode.PLATFORM_CAPTCHA, STILL_BLOCKED_HINT,
                           http_status=403) from e

    async def _crawl_once(self, sid: str, cities: list[str], position: str) -> list[dict]:
        page = await self.browser.get_context(sid)
        city_codes = await self._resolve_city_codes(page, cities)
        return await self._crawl(page, position, city_codes)

    async def _crawl(self, page, position: str, city_codes: list[str]) -> list[dict]:
        """按城市逐个翻页抓取，job_id 去重。

        listen 在 goto 之前开：首屏 joblist.json 也能收到，之后每滚一次到底触发下一页。
        """
        by_id: dict[str, dict] = {}
        query = urllib.parse.quote(position)
        for code in city_codes or [""]:           # 一个城市码都没有 → 退化成全国搜
            url = f"{BOSS_BASE_URL}{SEARCH_PATH}?query={query}"
            if code:
                url += f"&city={code}"
            await self.browser.listen_start(page, JOBLIST_API)
            try:
                await self.browser.guarded_goto(page, url, timeout_s=30)
                for _ in range(MAX_PAGES):
                    body = await self._wait_packet(page)
                    # 不跳页也可能直接在包里回码：zpData 空不等于"这个关键词没岗位"
                    _abort_on_api_risk(body)
                    for raw in ((body or {}).get("zpData") or {}).get("jobList") or []:
                        job = _normalize_job(raw)
                        if job is not None:
                            by_id.setdefault(job["job_id"], job)
                    await self.browser.scroll_bottom(page)
                    await asyncio.sleep(PAGE_INTERVAL_S)
            finally:
                await self.browser.listen_stop(page)
        return list(by_id.values())

    async def _wait_packet(self, page) -> dict | None:
        """等下一页的 joblist 包；等不到时再看一眼落地页，被弹走时包里根本没东西。"""
        body = await self.browser.listen_wait(page, LISTEN_TIMEOUT_S)
        if body is None:
            # 风控跳转是前端 JS 干的，goto 返回时 URL 还是搜索页，
            # 只有等不到包之后再查才看得到 verify.html / 403.html
            landed = await self.browser.tab_url(page)
            if is_blocked_url(landed):
                logger.warning("撞上风控，落在 %s", landed)
                raise RiskBlocked(risk_code_of(landed))
        return body

    async def _resolve_city_codes(self, page, cities: list[str]) -> list[str]:
        """城市名 → BOSS 城市码；认不出的跳过，全认不出就由 _crawl 退化成全国搜。"""
        codes = await self._load_city_codes(page)
        out: list[str] = []
        for name in cities:

            code = codes.get(name) or next((c for n, c in codes.items()
                                            if name in n or n in name), "")
            if not code:
                logger.warning("城市 %s 不在站点清单里，跳过", name)
            elif code not in out:
                out.append(code)
        return out

    async def _load_city_codes(self, page) -> dict[str, str]:
        """城市名 → BOSS 城市码：内存 → 磁盘缓存 → 站点接口。
        城市码是稳定枚举，落盘避免每次请求；缓存损坏/写失败都不影响检索。
        """
        if self._city_codes:
            return self._city_codes
        path = Path(settings.storage_root) / "city_codes.json"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached:
                self._city_codes = cached
                return cached
        except (OSError, json.JSONDecodeError):
            pass
        data = await self.browser.fetch_json(page, CITY_SITE_URL)
        # wapi 被风控时同步返回 {"code":36/32,"message":"…"}，比等搜索页跳转早 20 秒
        _abort_on_api_risk(data)
        zp = (data or {}).get("zpData") or {}
        items = list(zp.get("hotCitySites") or [])
        for group in zp.get("siteGroup") or []:
            items.extend(group.get("cityList") or [])
        self._city_codes = {str(c.get("name")): str(c.get("code"))
                            for c in items if c.get("name")}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._city_codes, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError as e:
            logger.warning("城市码缓存写入失败：%r", e)
        logger.info("城市站点表已加载：%d 个城市", len(self._city_codes))
        return self._city_codes

    @staticmethod
    def _resume_dict(state: DialogueState) -> dict:
        """resume_summary 是 JSON 字符串（action_save_resume 写入），补上意向字段。"""
        raw = state.get_slots("resume_summary") or ""
        try:
            resume = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("resume_summary 不是 JSON，按无技能继续匹配")
            resume = {}
        if not isinstance(resume, dict):
            resume = {}
        resume["intent_cities"] = state.get_slots("intent_cities") or ""
        resume["intent_salary"] = state.get_slots("intent_salary") or ""
        return resume
