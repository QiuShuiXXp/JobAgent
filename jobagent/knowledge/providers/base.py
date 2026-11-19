"""知识轨道的数据形状：检索请求与检索结果。"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class KnowledgeChunk:
    """检索到的知识内容"""
    content: str = ""


@dataclass(slots=True)
class KnowledgeQuery:
    intent: str
    objects: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeQuery":
        objects = {k: str(v) for k, v in (data.get("objects") or {}).items() if v}
        return cls(intent=data["intent"], objects=objects)