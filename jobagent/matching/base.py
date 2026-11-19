"""匹配结果的数据形状。"""
from dataclasses import dataclass, field


@dataclass(slots=True)
class MatchResult:
    score: float | None               # [0,1]；None = 模型没给分，不入排序
    reasons: list[str] = field(default_factory=list)