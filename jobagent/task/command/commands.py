from dataclasses import dataclass, fields
from typing import Any


@dataclass
class Command:

    @classmethod
    def from_dict(cls,data:dict[str,Any])->"Command":
        command_type=data.get("type")          # LLM 输出 {"type": "start_flow", "flow_id": ...}
        clz=COMMAND_TO_CLASS.get(command_type)
        if clz is None:
            raise ValueError(f"unknown command type: {command_type!r}")
        allowed={f.name for f in fields(clz)}  # 按目标类字段白名单过滤（LLM 多余键自动忽略）
        payload={k: v for k, v in data.items() if k in allowed}
        return clz(**payload)


@dataclass
class StartedFlowCommand(Command):
    flow_id:str = ""

@dataclass
class ResumedFlowCommand(Command):
    flow_id:str | None = None

@dataclass
class CancelFlowCommand(Command):
    pass

@dataclass
class SetSlotsCommand(Command):
    slots:dict[str,Any]


COMMAND_TO_CLASS: dict[str, type[Command]]={
    "start_flow": StartedFlowCommand,
    "resume_flow": ResumedFlowCommand,
    "cancel_flow": CancelFlowCommand,
    "set_slots": SetSlotsCommand,

}
