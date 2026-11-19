"""流程门禁（DAG）：流程声明依赖，前置流程全部结束才解锁。"""
from dataclasses import dataclass, field

from jobagent.task.flow.flows import Flow, FlowsList


@dataclass(slots=True)
class FlowGate:
    """流程依赖图：flow_id → 前置流程 id 列表 + 查询方法。"""
    depends_on: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_flows(cls, flow_list: FlowsList) -> FlowGate:
        """从流程声明构建依赖图（YAML 的 depends_on 字段）。"""
        return cls(depends_on={f.flow_id: list(f.depends_on) for f in flow_list.flows})

    def missing_flows(self, completed: set[str], flow_id: str) -> list[str]:
        """该流程还缺的前置流程（按声明顺序）。"""
        return [d for d in self.depends_on.get(flow_id, []) if d not in completed]

    def is_unlocked(self, completed: set[str], flow_id: str) -> bool:
        """所有前置流程都已完成 → 解锁。"""
        return not self.missing_flows(completed, flow_id)

    def unlocked_flows(self, flow_list: FlowsList, completed: set[str]) -> list[Flow]:
        return [f for f in flow_list.business_flows()
                if self.is_unlocked(completed, f.flow_id)]

    def locked_flows(self, flow_list: FlowsList, completed: set[str]) -> list[dict]:
        """未解锁流程 + 解锁它该开的前置：喂给规划器，把超前诉求引到主链路上。"""
        out = []
        for f in flow_list.business_flows():
            missing = self.missing_flows(completed, f.flow_id)
            if missing:
                out.append({"flow_id": f.flow_id, "name": f.flow_name,
                            "missing": missing,
                            "unlock_by_flow": missing[0]})
        return out

    def unlock_target(self, completed: set[str], flow_id: str) -> str | None:
        """被门禁挡住的流程该先开哪个前置（只补一级，且前置自己必须已解锁）。

        提示词第 2 条就是这句话术，但 LLM 未必照做——校验期用它做确定性兜底：
        用户说「筛选岗位」而还没登录，直接把登录流程开起来，比回一句澄清有用。
        """
        missing = self.missing_flows(completed, flow_id)
        if not missing:
            return None
        target = missing[0]
        if self.is_unlocked(completed, target):
            return target
        return None
