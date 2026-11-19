from dataclasses import dataclass


@dataclass(slots=True)
class FlowStepLink:
    target: str          # 目标步骤 id


@dataclass(slots=True)
class StaticLink(FlowStepLink):
    """无条件跳转：next: 目标"""


@dataclass(slots=True)
class ConditionLink(FlowStepLink):
    """条件边：- if: 表达式 / then: 目标"""
    condition: str


@dataclass(slots=True)
class FallbackLink(FlowStepLink):
    """回退边：- else: 目标（前面条件全不满足才走）"""
    pass