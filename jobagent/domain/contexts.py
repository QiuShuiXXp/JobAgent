from dataclasses import field, dataclass, asdict
from typing import Any


# 业务上下文
@dataclass(slots=True)
class TaskContext:
    """业务流程上下文：flow_id + 当前 step_id + 槽位字典（业务主键统一存这里）。"""
    flow_id:str
    step_id:str
    slots: dict[str,Any] = field(default_factory= dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls,data:dict):
        return cls(
            flow_id=data.get('flow_id'),
            step_id=data.get('step_id'),
            slots=data.get('slots', {})
        )

@dataclass(slots=True)
class SystemContext:

    flow_id: str
    step_id: str

    def to_dict(self):
        return asdict(self)


    @classmethod
    def from_dict(cls, data:dict):
        clz=FLOW_ID_TO_CONTEXT_CLASS[data['flow_id']]
        return clz(**data)


@dataclass(slots=True)
class StartedSystemContext(SystemContext):
    start_flow_id: str  # 要开启的业务流程ID
    start_flow_name: str # 要开启的业务流程名称


@dataclass(slots=True)
class ResumedSystemContext(SystemContext):
    resumable_flow_id: str  # 可恢复的业务流程ID
    resumable_flow_name: str # 可恢复的业务流程name


@dataclass(slots=True)
class InterruptedSystemContext(SystemContext):
    interrupted_flow_id: str  # 中断的业务流程ID
    interrupted_flow_name: str  # 中断的业务流程名字
    started_flow_id: str  # 开启新的业务流程ID
    started_flow_name: str  # 开启新的业务流程名字


@dataclass(slots=True)
class CanceledSystemContext(SystemContext):
    """
      触发时机  取消某一个已经开启的业务流程
      """
    canceled_flow_id: str  # 取消的业务流程ID
    canceled_flow_name: str  # 取消的业务流程名字

@dataclass(slots=True)
class CollectedSystemContext(SystemContext):

    response: dict[str, Any]
    slot_name: str  # 槽位名字

FLOW_ID_TO_CONTEXT_CLASS={
    "system_task_started":StartedSystemContext,
    "system_task_resumed":ResumedSystemContext,
    "system_task_interrupted":InterruptedSystemContext,
    "system_task_canceled":CanceledSystemContext,
    "system_collect_information":CollectedSystemContext
}
