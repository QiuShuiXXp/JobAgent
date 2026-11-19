
"""轻量日志基座：标准库 logging + JSON Formatter + contextvars 透传 correlation_id。

规避缺陷 #4：不用 structlog；JSON 行手工拼装，字段固定：
timestamp / level / logger / message / correlation_id / user_id / task_id
"""
import contextvars
import json
import logging
import sys
import uuid

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default="-")
task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_id", default="-")


def new_correlation_id() -> str:
    return f"t_{uuid.uuid4().hex[:12]}"


def get_correlation_id() -> str:
    return correlation_id_var.get()


def bind_user(user_id: str) -> None:
    """服务层绑定当前用户上下文（对话引擎/任务执行协程入口调用）。"""
    user_id_var.set(user_id)


def bind_task(task_id: str) -> None:
    """任务执行协程绑定 task_id。"""
    task_id_var.set(task_id)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
            "user_id": user_id_var.get(),
            "task_id": task_id_var.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    from jobagent.config import paths
    if paths.is_desktop():
        # 桌面壳里进程没有控制台，出问题只能翻文件
        from logging.handlers import RotatingFileHandler
        paths.log_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(
            paths.log_dir() / "jobagent.log", maxBytes=2_000_000,
            backupCount=3, encoding="utf-8"))
    formatter = JsonFormatter()
    for handler in handlers:
        handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level.upper())