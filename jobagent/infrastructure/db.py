
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from sqlalchemy import event, make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,)
from sqlalchemy.pool import StaticPool
import jobagent.model.business_models
import jobagent.model.state_record
import jobagent.model.session_record
from jobagent.config.settings import settings

engine: AsyncEngine | None = None
session_factory:async_sessionmaker[AsyncSession] | None = None

def init_db_engine()->None:
    global engine,session_factory

    url = make_url(settings.data_base_url)
    # timeout 就是 SQLite 的 busy_timeout：写冲突时排队等，而不是直接抛 database is locked
    kwargs: dict = {"pool_pre_ping": True,
                    "connect_args": {"check_same_thread": False, "timeout": 5}}
    if not url.database or url.database == ":memory:":
        kwargs["poolclass"] = StaticPool      # 内存库必须复用同一条连接，否则换个连接就空表

    engine = create_async_engine(settings.data_base_url, **kwargs)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    session_factory=async_sessionmaker(engine,expire_on_commit=False)


async def create_tables()->None:
    """按 ORM 元数据建表（缺失才建，已有不动）：桌面端首启就是它建出本地库。"""
    import jobagent.model.business_models  # noqa: F401 导入即注册进 Base.metadata
    import jobagent.model.state_record     # noqa: F401
    from jobagent.model.base import Base

    assert engine is not None, "call init_db_engine() first"
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # login_credentials 是 cookie 登录之前的遗物（2026-10-18 起无任何读写），老库顺手清掉
        await conn.execute(text("DROP TABLE IF EXISTS login_credentials"))
        # version 是从未启用的乐观锁列：ORM 已删，老库留着会让 INSERT 撞 NOT NULL
        await conn.run_sync(_drop_legacy_version_column)


def _drop_legacy_version_column(conn) -> None:
    from sqlalchemy import inspect
    insp = inspect(conn)
    if insp.has_table("dialogue_states") and any(
            c["name"] == "version" for c in insp.get_columns("dialogue_states")):
        conn.execute(text("ALTER TABLE dialogue_states DROP COLUMN version"))

async def dispose_engine()->None:
    if engine is not None:
        await engine.dispose()
@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """请求级会话：成功提交、异常回滚（D7 起也可经 FastAPI Depends 注入）。"""
    assert session_factory is not None, "call init_db_engine() first"
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
