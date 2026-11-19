from dataclasses import dataclass, field

from jobagent.task.flow.steps import FlowStep, StartFlowStep


@dataclass(slots=True)
class Flow:
    flow_id: str
    flow_name: str
    description: str
    steps: list[FlowStep] = field(default_factory=list)
    # 重进/恢复时回到 start 重走一遍（重弹窗口类流程用，见 user_flows.yml boss_login）
    restart_on_reentry: bool = False
    # 前置流程依赖（门禁）：depends_on 的流程全部结束才解锁
    depends_on: list[str] = field(default_factory=list)

    # 获取开始步骤id
    def get_start_step(self)->StartFlowStep | None:
        for step in self.steps:
            if isinstance(step,StartFlowStep):
                return step
        return None
    # 获取步骤
    def get_step_by_id(self,step_id:str):
        for step in self.steps:
            if step.id == step_id:
                return step
        return None


"""
加载两份yml
"""
@dataclass(slots=True)
class FlowsList:
    flows:list[Flow] = field(default_factory=list)
    slots: set[str] = field(default_factory=set)   # 已定义槽位名（collect 引用校验用）


    def business_flows(self)->list[Flow]:
        return  [f for f in self.flows if not f.flow_id.startswith("system_")]

    def get_flow_by_id(self,flow_id:str):
        for flow in self.flows:
            if flow.flow_id == flow_id:
                return flow
        return None