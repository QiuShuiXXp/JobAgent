
import asyncio
import logging
from collections import defaultdict

from jobagent.config.settings import settings
from jobagent.domain.state import DialogueState, Session
from jobagent.history.builder import ChatHistoryBuilder
from jobagent.infrastructure.db import get_session
from jobagent.infrastructure.llm_client import get_llm
from jobagent.prompts.loader import PromptLoader
from jobagent.repository.session_repository import SessionRepository

logger = logging.getLogger("jobagent.summarizer")

"""情景记忆：把会话滚动折叠成一段「前情提要」。

三个入口：
  maybe_fold     每轮提交后调（engine.on_turn_committed）——每 fold_every 轮折一次
  close_session  会话超时关闭时调（engine.on_session_closed）——折剩余尾部
  latest_context planner 每轮调——取最近一次折叠的成果（可能是当前会话的半场）

"""
class SessionSummarizer:
    def __init__(self, loader: PromptLoader) -> None:
        self.loader = loader
        self.fold_every = settings.session_summary_fold_every
        self.min_turns = settings.session_summary_min_turns
        self.max_chars = settings.session_summary_max_chars
        self._tasks: set[asyncio.Task] = set()
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def latest_context(self, sender_id: str) -> str:
        """planner 每轮调用：最近折叠的一条摘要；失败降级为空串——情景记忆是增强，不是依赖。"""
        try:
            async with get_session() as session:
                return await SessionRepository(session).latest_summary(sender_id)
        except Exception:
            logger.warning("读取最近摘要失败：%s", sender_id, exc_info=True)
            return ""

    def maybe_fold(self, state: DialogueState) -> None:
        """
        每 fold_every 轮折一次。同步返回，绝不阻塞本轮回复。
        """
        session = state.current_session()
        if session is None:
            return
        n = len(session.turns)
        if n and not n % self.fold_every:
            self._schedule(state.sender_id, session)

    def close_session(self, state: DialogueState) -> None:
        """会话关闭：折剩余尾部。同步返回——整条路径都不碰用户请求链路。"""
        closed = state.last_closed_session()
        if closed is not None:
            self._schedule(state.sender_id, closed)

    def _schedule(self, sender_id: str, session: Session) -> None:
        task = asyncio.create_task(self._fold(sender_id, session))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fold(self, sender_id: str, session: Session) -> None:
        try:
            async with self._locks[session.session_id]:
                if len(session.turns) < self.min_turns:
                    return
                async with get_session() as db:
                    repo = SessionRepository(db)
                    folded, old = await repo.fold_state(session.session_id)
                    pending = session.turns[folded:]   # 切片即快照：折叠期间新到的轮次不算这次
                    if not pending:
                        return
                    history = ChatHistoryBuilder.build(pending)
                    if not history.strip():
                        return
                    prompt = self.loader.render("session_summary.j2",
                                                old_summary=old,
                                                history=history,
                                                max_chars=self.max_chars)
                    summary = ((await get_llm().ainvoke(prompt)).content or "").strip()
                    summary = summary[:self.max_chars]
                    if not summary:
                        return
                    await repo.save_fold(session.session_id, sender_id, summary,
                                         folded + len(pending))
                    logger.info("会话 %s 已折叠到第 %d/%d 轮",
                                session.session_id, folded + len(pending), len(session.turns))
        except Exception:

            logger.warning("折叠会话 %s 失败", session.session_id, exc_info=True)
