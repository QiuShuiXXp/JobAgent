"""路由：/hello、/api/chat、/api/upload、/api/login_status、/api/panel、/api/tasks/{task_id}（打招呼结果）。"""
import asyncio
import uuid

from fastapi import APIRouter, UploadFile, File

from jobagent.api.dependencies import (
    DialogueServiceDep, ResumeServiceDep, TaskManagerDep, BrowserManagerDep,
)
from jobagent.api.schemas import (
    BotMessageOut, CardObjectOut, ChatRequest, ChatResponse,
    FocusedObjectIn, LoginStatusOut, PanelActionIn, PanelStateOut,
    TaskItemOut, TaskResultOut, UploadResponse,
)
from jobagent.domain.messages import FocusedObject, MessageType, UserMessage
from jobagent.api.errors import AppError, ErrorCode
from jobagent.infrastructure.db import get_session
from jobagent.repository.dialogue_repository import DialogueRepository

router = APIRouter()


@router.get("/hello")
async def hello() -> dict:
    return {"message": "Hello, JobAgent!"}


@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, service: DialogueServiceDep) -> ChatResponse:
    # ---- schema → domain 显式转换 ----
    um = UserMessage(
        message_id=str(uuid.uuid4()),
        type=MessageType.OBJECT if req.type == "OBJECT" else MessageType.TEXT,
        text=req.text,
        object=FocusedObject(type=req.object.type, id=req.object.id,
                             payload=req.object.payload) if req.object else None)
    result = await service.handle_chat(req.sender_id, um)
    # ---- domain → schema 显式转换 ----
    return ChatResponse(sender_id=result.sender_id, messages=[
        BotMessageOut(type=m.type.value, text=m.text,
                      object=CardObjectOut(type=m.object.type, id=m.object.id,
                                           payload=m.object.payload)
                       if m.object else None,
                      choices=m.choices)
        for m in result.messages])


@router.post("/api/upload", response_model=UploadResponse)
async def upload(resume_service: ResumeServiceDep, file: UploadFile = File(...)) -> UploadResponse:
    raw = await file.read()
    size = len(raw)
    filename = file.filename or "unknown"
    # 复用 resume_service 校验（1004/1003/2001 三态）
    head = raw[:8]
    resume_service.validate(filename, size, head)
    file_ref = resume_service.file_store.save(raw, filename)
    return UploadResponse(file_ref=file_ref, filename=filename, size=size)

@router.get("/api/login_status", response_model=LoginStatusOut)
async def login_status(sender_id: str, browser: BrowserManagerDep) -> LoginStatusOut:
    """
    登录页体检：前端在登录卡片期间轮询它，logged_in=true 就替用户自动提交一次确认。
    只读现有实例的浏览器级 cookie，不会为了探测把浏览器拉起来。
    """
    opened = browser.has_context(sender_id) and browser.is_presented()
    return LoginStatusOut(window_open=opened, logged_in=await browser.logged_in(sender_id))

@router.get("/api/panel", response_model=PanelStateOut)
async def panel_state(browser: BrowserManagerDep) -> PanelStateOut:
    """BOSS 左栏状态：supported=false 时前端不显示折叠按钮（dev 形态没有左栏）。"""
    return PanelStateOut(supported=browser.viewport.has_panel,
                         presented=browser.is_presented())


@router.post("/api/panel", response_model=PanelStateOut)
async def panel_action(req: PanelActionIn, browser: BrowserManagerDep) -> PanelStateOut:
    """用户折叠/展开 BOSS 左栏。收起只是挪出可视区，页面在后台照常渲染。"""
    if not browser.viewport.has_panel:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "当前形态没有可折叠的 BOSS 左栏", http_status=400)
    # 壳指令走本机 TCP（至多 3 秒），别在事件循环里干等
    act = browser.collapse if req.action == "collapse" else browser.present
    ok = await asyncio.to_thread(act)
    if not ok:
        raise AppError(ErrorCode.BROWSER_UNAVAILABLE,
                       "桌面壳没有响应左栏切换：请确认应用窗口还在运行", http_status=503)
    return PanelStateOut(supported=True, presented=browser.is_presented())


@router.get("/api/tasks/{task_id}", response_model=TaskResultOut)
async def task_result(task_id: str, manager: TaskManagerDep) -> TaskResultOut:
    """打招呼结果：前端在收到 task_report 卡片后轮询它，finished=true 时展示汇总。"""
    result = await manager.result(task_id)
    if result is None:
        raise AppError(ErrorCode.NOT_FOUND, "打招呼任务不存在", http_status=404)
    return TaskResultOut(**result)


@router.delete("/api/history/{sender_id}")
async def clear_history(sender_id: str) -> dict:
    """
    清空历史：历史消息、流程断点、意向/简历/登录槽与折叠摘要，从头重走流程。
    """
    if not sender_id.strip():
        raise AppError(ErrorCode.INVALID_ARGUMENT, "sender_id 不能为空")
    async with get_session() as session:
        result = await DialogueRepository(session).clear_history(sender_id)
    return {"sender_id": sender_id, **result}