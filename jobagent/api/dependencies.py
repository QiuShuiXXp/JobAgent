"""依赖注入：Annotated[..., Depends()] 链式注入。"""
from typing import Annotated

from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from jobagent.infrastructure import db

# ---- DB session（每请求新建，yield 自动管理生命周期）----
async def get_session():
    async with db.session_factory() as session:
        yield session

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# ---- 引擎（应用级单例，lifespan 调 init_engine 写入全局）----
from jobagent.engine.dialogue_engine import DialogueEngine

_engine: DialogueEngine | None = None

def init_engine(engine: DialogueEngine) -> None:
    global _engine
    _engine = engine

def get_engine() -> DialogueEngine:
    return _engine

EngineDep = Annotated[DialogueEngine, Depends(get_engine)]

# ---- 对话状态仓储（每请求新建）----
from jobagent.repository.dialogue_repository import DialogueRepository

def get_dialogue_repository(session: SessionDep) -> DialogueRepository:
    return DialogueRepository(session=session)

DialogueRepositoryDep = Annotated[DialogueRepository, Depends(get_dialogue_repository)]

# ---- 对话服务（每请求新建）----
from jobagent.services.dialogue_service import DialogueService

def get_dialogue_service(
    repository: DialogueRepositoryDep,
    engine: EngineDep,
) -> DialogueService:
    return DialogueService(repository=repository, engine=engine)

DialogueServiceDep = Annotated[DialogueService, Depends(get_dialogue_service)]

# ---- BrowserManager（应用级单例，lifespan 装配）----
from jobagent.infrastructure.browser import BrowserManager

_browser_manager: BrowserManager | None = None

def init_browser_manager(bm: BrowserManager) -> None:
    global _browser_manager
    _browser_manager = bm

def get_browser_manager() -> BrowserManager:
    return _browser_manager

BrowserManagerDep = Annotated[BrowserManager, Depends(get_browser_manager)]

# ---- ResumeService（应用级单例，lifespan 装配）----
from jobagent.services.resume_service import ResumeService

_resume_service: ResumeService | None = None

def init_resume_service(rs: ResumeService) -> None:
    global _resume_service
    _resume_service = rs

def get_resume_service() -> ResumeService:
    return _resume_service

ResumeServiceDep = Annotated[ResumeService, Depends(get_resume_service)]

# ---- TaskManager（应用级单例，lifespan 装配；/api/tasks 读打招呼结果）----
from jobagent.services.task_manager import TaskManager

_task_manager: TaskManager | None = None

def init_task_manager(tm: TaskManager) -> None:
    global _task_manager
    _task_manager = tm

def get_task_manager() -> TaskManager:
    return _task_manager

TaskManagerDep = Annotated[TaskManager, Depends(get_task_manager)]