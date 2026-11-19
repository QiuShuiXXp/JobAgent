from pathlib import Path
from typing import Any
import yaml

from jobagent.task.flow.flows import Flow, FlowsList
from jobagent.task.flow.steps import FlowStep, CollectFlowStep


class FlowLoader:
    def load_many(self, paths: list[str | Path]) -> FlowsList:
        flows: list[Flow] = []
        slots: set[str] = set()
        for path in paths:
            loaded = self.load(Path(path))
            flows.extend(loaded.flows)
            # 跨文件重复槽位检测：加载期响亮失败，好过运行期悄悄覆盖
            dup = slots.intersection(loaded.slots)
            if dup:
                raise ValueError(f"Duplicate slot definitions: {sorted(dup)}")
            slots.update(loaded.slots)
        return FlowsList(flows=flows, slots=slots)

    def load(self, path: Path):
        """加载 yml：顶层 slots 块只取槽位名（label/description 是给人看的文档）。"""
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        slots = set(data.get("slots") or {})
        flows = self._load_flows(data.get("flows", {}))
        self._validate(flows, slots)
        return FlowsList(flows=flows, slots=slots)

    def _load_flows(self, flows_data: dict[str, Any]):
        flows: list[Flow] = []
        for flow_id, flow_data in flows_data.items():
            steps = [FlowStep.from_dict(s) for s in flow_data.get("steps", [])]
            flows.append(
                Flow(
                    flow_id=flow_id,
                    flow_name=flow_data.get("name", ""),
                    description=flow_data.get("description", ""),
                    steps=steps,
                    restart_on_reentry=bool(flow_data.get("restart_on_reentry", False)),
                    depends_on=list(flow_data.get("depends_on", [])),
                )
            )
        return flows

    def _validate(self, flows: list[Flow], slots: set[str]) -> None:
        """fail-fast 四查：槽位已定义 / flow_id 无重名 / next 引用存在 / 依赖流程存在。"""
        ids = [f.flow_id for f in flows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate flow_id: {ids}")
        flow_ids = set(ids)
        for flow in flows:
            for dep in flow.depends_on:
                if dep not in flow_ids:
                    raise ValueError(
                        f"flow={flow.flow_id}: depends_on '{dep}' not found")
            step_ids = {s.id for s in flow.steps}
            for step in flow.steps:
                if isinstance(step, CollectFlowStep) and step.slot_name not in slots:
                    raise ValueError(
                        f"flow={flow.flow_id} step={step.id}: "
                        f"slot '{step.slot_name}' not defined in top-level slots")
                for link in step.next:
                    if link.target not in step_ids:
                        raise ValueError(
                            f"flow={flow.flow_id} step={step.id}: "
                            f"next target '{link.target}' not found")