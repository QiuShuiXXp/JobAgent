"""节点：4 类步骤 + from_dict 反序列化 + next 解析（翻译核心）。"""
from dataclasses import field, dataclass
from enum import Enum
from typing import Any

from jobagent.task.flow.links import FlowStepLink, StaticLink, ConditionLink, FallbackLink


@dataclass(slots=True)
class ResponseDefinition:
    mode: str = "static"               # static 原样输出 / rephrase LLM 润色（D3 接）
    text: str | None = None
    prompt: str | None = None          # rephrase 模式的改写提示
    choices: list[str] = field(default_factory=list)   # 快捷回复按钮（前端渲染，点击=发送对应文本）


@dataclass(slots=True)
class SlotValidation:
    condition: str | None = None       # validate 表达式（simpleeval 求值）
    failure_response: ResponseDefinition | None = None   # 校验失败话术（YAML 可配，缺省走默认话术）
class  FlowStepType(Enum):
    """
    step类型
    """
    START = "start"
    ACTION= "action"
    COLLECT= "collect"
    END = "end"

@dataclass(slots=True)
class FlowStep:
    id:str
    type:FlowStepType       # 到时候可以替换成 start | action | collect | end
    description: str = ""
    next: list[FlowStepLink] = field(default_factory= list)

    @classmethod
    def from_dict(cls,step_data:dict[str,Any])->"FlowStep":
        step_type = step_data["type"]
        clz=STEP_TYPE_TO_CLASS[step_type]
        return clz.from_dict(step_data)

        # ---- 翻译核心 2：抽取公共字段 ----

    @staticmethod
    def base_fields(step_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": step_data["id"],
            "type": FlowStepType(step_data["type"]),
            "description": step_data.get("description", ""),
            "next": FlowStep.build_links(step_data["next"]),
        }

    # ---- 翻译核心 3：next 字段 → 边列表 ----
    @staticmethod
    def build_links(next_data: str | list) -> list[FlowStepLink]:
        if isinstance(next_data, str):
            return [StaticLink(target=next_data)]
        links: list[FlowStepLink] = []
        for link_data in next_data:
            if "if" in link_data:
                links.append(ConditionLink(
                    target=link_data["then"], condition=link_data["if"]))
            else:
                links.append(FallbackLink(target=link_data["else"]))
        return links


@dataclass(slots=True)
class StartFlowStep(FlowStep):
    @classmethod
    def from_dict(cls,step_data:dict[str,Any]) ->"StartFlowStep":
        return cls(**FlowStep.base_fields(step_data))

@dataclass(slots=True)
class EndFlowStep(FlowStep):
    @classmethod
    def from_dict(cls, step_data: dict[str, Any]) -> "EndFlowStep":
        return cls(**FlowStep.base_fields(step_data))

@dataclass(slots=True)
class ActionFlowStep(FlowStep):
    action: str =""
    args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, step_data: dict[str, Any]) -> "ActionFlowStep":
        return cls(**FlowStep.base_fields(step_data),
                   action=step_data["action"],
                   args=step_data.get("args", {}))

@dataclass(slots=True)
class CollectFlowStep(FlowStep):
    slot_name: str=""
    response: ResponseDefinition = field(default_factory=ResponseDefinition)
    validation: SlotValidation | None = None

    @classmethod
    def from_dict(cls, step_data: dict[str, Any]) -> "CollectFlowStep":
        return cls(
            **FlowStep.base_fields(step_data),
            slot_name=step_data["slot_name"],
            response=ResponseDefinition(**step_data.get("response", {})),
            validation=SlotValidation(
                condition=step_data["validation"]["condition"],
                failure_response=ResponseDefinition(**step_data["validation"]["failure_response"])
                if step_data["validation"].get("failure_response") else None,
            )
            if "validation" in step_data else None,
        )

STEP_TYPE_TO_CLASS ={
    "start":StartFlowStep,
    "action":ActionFlowStep,
    "collect":CollectFlowStep,
    "end":EndFlowStep
}
