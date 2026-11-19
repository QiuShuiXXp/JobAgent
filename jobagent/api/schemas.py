"""接口模型：与 domain 显式转换（禁模型互渗）。"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class FocusedObjectIn(BaseModel):
    type: Literal["resume", "job_list", "job", "login_window", "task_report"]
    id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    sender_id: str
    type: Literal["TEXT", "OBJECT"] = "TEXT"
    text: str | None = None            # TEXT 必填
    object: FocusedObjectIn | None = None   # OBJECT 必填


class CardObjectOut(BaseModel):
    type: str
    id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class BotMessageOut(BaseModel):
    type: Literal["TEXT", "OBJECT"] = "TEXT"
    text: str | None = None
    object: CardObjectOut | None = None
    choices: list[str] = Field(default_factory=list)   # 快捷回复按钮，空=纯文本消息


class ChatResponse(BaseModel):
    sender_id: str
    messages: list[BotMessageOut]
    session_id: str | None = None
    active_flow: str | None = None


class UploadResponse(BaseModel):
    file_ref: str
    filename: str
    size: int

class PanelStateOut(BaseModel):
    supported: bool      # 桌面壳内嵌左栏才有得折叠；dev 形态 False，前端不显示按钮
    presented: bool      # 左栏当前是否展开


class PanelActionIn(BaseModel):
    action: Literal["collapse", "reveal"]


class LoginStatusOut(BaseModel):
    window_open: bool          # 登录窗口还开着（有头实例存在）
    logged_in: bool            # profile 已带上 BOSS 的 auth cookie

class TaskItemOut(BaseModel):
    job_id: str
    title: str
    item_status: str          # PENDING/SUCCEEDED/DRY_RUN/FAILED
    failure_reason: str


class TaskResultOut(BaseModel):
    task_id: str
    status: str               # PENDING/RUNNING/SUCCESS/PARTIAL_SUCCESS/FAILED
    finished: bool
    dry_run: bool
    total: int
    succeeded: int
    failed: int
    items: list[TaskItemOut]


class LlmSettingsIn(BaseModel):
    api_key: str | None = None        # 留空 = 不改已存的 Key
    base_url: str | None = None
    model: str | None = None
    search_api_key: str | None = None  # 联网搜索（百度千帆）的 Key，留空 = 不改


class LlmSettingsOut(BaseModel):
    llm_key_set: bool
    llm_key_masked: str        # 只回末 4 位，明文密钥永不回前端
    llm_key_store: str         # 凭据管理器 / 配置文件 / 仓库 .env / 空=未配
    llm_model: str
    llm_base_url: str
    search_key_set: bool
    search_key_masked: str
    search_key_store: str
    desktop: bool