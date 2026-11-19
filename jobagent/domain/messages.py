from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(Enum):
    TEXT = "TEXT"
    OBJECT = "OBJECT"


# 卡片对象类型（JobAgent 扩展）
CARD_TYPES = ("resume", "job_list", "job", "login_window", "task_report")


@dataclass(slots=True)
class FocusedObject:
    """用户"聚焦"的卡片对象（上传的简历 / 勾选的岗位列表 / 登录窗口…）。"""
    type: str
    id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FocusedObject":
        return cls(type=data["type"], id=data["id"], payload=data.get("payload", {}))


@dataclass(slots=True)
class UserMessage:
    message_id: str
    type: MessageType = MessageType.TEXT
    text: str | None = None
    object: FocusedObject | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "type": self.type.value,
            "text": self.text,
            "object": self.object.to_dict() if self.object else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserMessage":
        return cls(
            message_id=data["message_id"],
            type=MessageType(data.get("type", "TEXT")),
            text=data.get("text"),
            object=FocusedObject.from_dict(data["object"]) if data.get("object") else None,
        )


@dataclass(slots=True)
class BotMessage:
    type: MessageType = MessageType.TEXT
    text: str | None = None
    object: FocusedObject | None = None
    choices: list[str] = field(default_factory=list)   # 快捷回复按钮，空=纯文本消息

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "text": self.text,
            "object": self.object.to_dict() if self.object else None,
            "choices": self.choices,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BotMessage":
        return cls(
            type=MessageType(data.get("type", "TEXT")),
            text=data.get("text"),
            object=FocusedObject.from_dict(data["object"]) if data.get("object") else None,
            choices=data.get("choices") or [],
        )


@dataclass(slots=True)
class Turn:
    turn_id: str
    user_message: UserMessage
    bot_messages: list[BotMessage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "user_message": self.user_message.to_dict(),
            "bot_messages": [bm.to_dict() for bm in self.bot_messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Turn":
        return cls(
            turn_id=data["turn_id"],
            user_message=UserMessage.from_dict(data["user_message"]),
            bot_messages=[BotMessage.from_dict(b) for b in data.get("bot_messages", [])],
        )


@dataclass(slots=True)
class ProcessResult:
    sender_id: str
    message_id: str
    messages: list[BotMessage]
