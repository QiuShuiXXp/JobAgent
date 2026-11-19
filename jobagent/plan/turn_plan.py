from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# turn_plan数据模型

@dataclass(slots=True)
class TaskTurnPlan:
    commands:list[dict[str,Any]]=field(default_factory=list)




@dataclass(slots=True)
class KnowledgeTurnPlan:
    intents: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TurnPlan:

    task: TaskTurnPlan | None = None
    knowledge: KnowledgeTurnPlan | None = None

    def hit_tracks(self)->list[str]:
        tracks=[]
        if self.task is not None:
            tracks.append('task')
        if self.knowledge is not None:
            tracks.append('knowledge')
        return tracks


class ClarifyReason(Enum):
    MISSING_TRACK = "missing_track"                    # 一条轨道都没命中
    MULTIPLE_TRACKS = "multiple_tracks"                # 命中多条
    MISSING_TASK_COMMANDS = "missing_task_commands"    # task 轨道 commands 为空
    INVALID_TASK_COMMANDS = "invalid_task_commands"    # 命令类型不在白名单
    MULTIPLE_TASK_FLOWS = "multiple_task_flows"        # start_flow 超过 1 个
    UNKNOWN_TASK_FLOW = "unknown_task_flow"            # flow 不存在
    FLOW_LOCKED = "flow_locked"                        # flow 存在但前置未解锁（主链路门禁）

    MISSING_FOCUSED_OBJECT = "missing_focused_object"


@dataclass(slots=True)
class TurnPlanValidateResult:
    valid: bool
    reason:ClarifyReason|None = None
    # FLOW_LOCKED 时带回被门禁挡住的那个 flow，引擎据此直接去开它的前置流程
    flow_id: str | None = None