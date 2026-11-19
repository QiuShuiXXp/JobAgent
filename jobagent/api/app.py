"""FastAPI 工厂 + lifespan：D1 最小版升级为完整装配。"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from jobagent.api.errors import AppError, ErrorCode
from jobagent.infrastructure.log_config import (
    configure_logging, get_correlation_id, new_correlation_id,
)
from jobagent.config.paths import ASSETS_ROOT
from jobagent.config.settings import settings

logger = logging.getLogger("jobagent.app")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ---- 启动链（顺序不可乱）----
        from jobagent.config import paths
        # 两种形态都装 handler：dev 只进 stdout，桌面再加滚动文件。
        # 不装的话 dev 里 INFO 会被丢掉——简历解析耗时这类诊断日志正是 INFO。
        configure_logging(settings.log_level)
        if paths.is_desktop():
            # 首启可能没跑过 init_desktop，运行目录得自己长出来，否则 SQLite 打不开
            paths.ensure_app_home()
            from jobagent.infrastructure import instance_lock
            try:                                   # ① 单实例锁，抢不到就不必再往下
                instance_lock.acquire()
            except instance_lock.AlreadyRunning as exc:
                logger.error("单实例锁拦下了这次启动：%s", exc)
                raise SystemExit(2) from None
        # 配置来源留一行：出过「.env 里明明配了却说没配」，看这行就知道进程读的是哪份文件。
        # keys 只报名字不报值。
        logger.info("配置：形态=%s 文件=%s 密钥=%s",
                    "desktop" if paths.is_desktop() else "dev", paths.config_file(),
                    ",".join(name for name, value in (
                        ("LLM_API_KEY", settings.llm_api_key),
                        ("QIANFAN_API_KEY", settings.qianfan_api_key)) if value) or "-")
        from jobagent.infrastructure.db import init_db_engine, create_tables
        init_db_engine()                                     # ① 数据库引擎
        await create_tables()                                # ①b 建表（桌面端首启建本地库）
        from jobagent.prompts.loader import PromptLoader
        PromptLoader().dry_render_check()                    # ② 提示词自检（fail-fast）
        from jobagent.services.resume_service import ResumeService
        from jobagent.infrastructure.file_store import FileStore
        file_store = FileStore(root=settings.storage_root)   # ③ 简历文件存储（明文落盘）
        resume_service = ResumeService(                      # ④ 简历服务（引擎依赖）
            file_store=file_store,
            extract_template=PromptLoader().raw("resume_extract.j2"))
        from jobagent.infrastructure.browser import BrowserManager
        from jobagent.infrastructure.viewport import build_viewport
        # 形态在这里定一次：壳给了调试端口就接管内嵌 WebView2，否则后端自己拉本机 Chrome
        viewport = build_viewport()
        browser_manager = BrowserManager(viewport)   # ⑤ 浏览器管理器（登录/检索动作依赖）
        logger.info("浏览器形态：%s", viewport.describe())
        from jobagent.matching.llm import LlmMatchProvider
        from jobagent.services.match_service import MatchService
        match_service = MatchService(provider=LlmMatchProvider(
            template_text=PromptLoader().raw("match_score.j2")))  # ⑥ 匹配服务（LLM 单次批量打分）
        from jobagent.services.greet_executor import GreetExecutor
        from jobagent.services.task_manager import TaskManager
        task_manager = TaskManager(GreetExecutor(browser_manager),
                                   browser_manager)         # ⑦ 打招呼后台任务
        from jobagent.engine.builder import build_engine
        engine = build_engine(resume_service, browser_manager, match_service,
                              task_manager)                # ⑧ 引擎装配（FlowLoader 启动校验在内）
        from jobagent.api.dependencies import (
            init_engine, init_browser_manager, init_resume_service, init_task_manager,
        )
        init_engine(engine)                                  # ⑨ 引擎写入全局
        init_browser_manager(browser_manager)                # ⑩ 浏览器管理器写入全局
        init_resume_service(resume_service)                  # ⑪ 简历服务写入全局
        init_task_manager(task_manager)                      # ⑫ 任务结果查询写入全局
        logger.info("启动完成")
        yield
        # ---- 退出逆序销毁 ----
        await task_manager.shutdown()                        # 先停执行协程，协程里还在用浏览器
        from jobagent.api.dependencies import get_browser_manager
        await get_browser_manager().stop()
        from jobagent.infrastructure.db import dispose_engine
        await dispose_engine()

    app = FastAPI(title="JobAgent", lifespan=lifespan)

    # ---- trace 中间件：correlation_id 生成/透传 ----
    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or new_correlation_id()
        from jobagent.infrastructure.log_config import correlation_id_var
        correlation_id_var.set(trace_id)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        # 一轮聊天可能串了规划器 + 简历解析两次 LLM：只打总耗时，配合 resume parse 那行看谁慢
        if request.url.path.startswith("/api/"):
            logger.info("%s %s → %s，用时 %.1fs", request.method, request.url.path,
                        response.status_code, time.perf_counter() - started)
        # 静态页/接口都不做启发式缓存：StaticFiles 默认不发 Cache-Control，
        # 浏览器会按 Last-Modified 猜有效期，改过 chat.js 后用户拿到的还是旧文件
        if request.method == "GET":
            response.headers["Cache-Control"] = "no-store"
        return response

    # ---- 全局异常处理器----
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content={
            "code": exc.error.code, "message": exc.message,
            "trace_id": get_correlation_id(), "data": None})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "code": 1001, "message": "参数不合法",
            "trace_id": get_correlation_id(), "data": None})

    from jobagent.infrastructure.browser import BrowserUnavailableError

    @app.exception_handler(BrowserUnavailableError)
    async def browser_missing_handler(request: Request,
                                      exc: BrowserUnavailableError) -> JSONResponse:
        # 没浏览器是分发场景最常见的失败，绝不能兜成「错误 1005：内部错误」让用户瞎猜
        logger.error("浏览器不可用：%s", exc)
        return JSONResponse(status_code=503, content={
            "code": ErrorCode.BROWSER_UNAVAILABLE.code, "message": str(exc),
            "trace_id": get_correlation_id(), "data": None})

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理的异常")
        return JSONResponse(status_code=500, content={
            "code": 1005, "message": "内部错误",
            "trace_id": get_correlation_id(), "data": None})

    # ---- 路由与静态页 ----
    from jobagent.api.router.chat_router import router as chat_router
    from jobagent.api.router.settings_router import router as settings_router
    app.include_router(chat_router)
    app.include_router(settings_router)
    app.mount("/", StaticFiles(directory=str(ASSETS_ROOT / "web"), html=True), name="web")
    return app


app = create_app()