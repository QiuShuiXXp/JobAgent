from jobagent.domain.state import DialogueState
from jobagent.knowledge.intents import KNOWLEDGE_INTENTS
from jobagent.plan.turn_plan import TurnPlan, TurnPlanValidateResult, ClarifyReason
from jobagent.task.command.commands import COMMAND_TO_CLASS
from jobagent.task.flow.flows import FlowsList
from jobagent.task.flow.gates import FlowGate

"""
校验器：校验llm生成的命令
"""
class TurnPlanValidator:
    def __init__(self, flow_list: FlowsList, flow_gate: FlowGate = None) -> None:
        self.flow_list = flow_list
        self.flow_gate = flow_gate or FlowGate()

    def validate(self,turn_plan:TurnPlan,state:DialogueState):

        tracks=turn_plan.hit_tracks()

        # 没有命中轨道
        if not tracks:
            return TurnPlanValidateResult(False,ClarifyReason.MISSING_TRACK)
        # 命中多个轨道
        if len(tracks) > 1:
            return TurnPlanValidateResult(False,ClarifyReason.MULTIPLE_TRACKS)


        # 校验业务轨道
        if turn_plan.task is not None:
            return self._validate_task(turn_plan, state)

        # 校验知识库轨道

        if turn_plan.knowledge is not None:
            return self._knowledge(turn_plan, state)

        # 兜底
        return TurnPlanValidateResult(False, ClarifyReason.MISSING_TRACK)

    def _validate_task(self, turn_plan, state):
        # 业务轨道校验
        commands=turn_plan.task.commands

        #
        if not commands:
            return TurnPlanValidateResult(False,ClarifyReason.MISSING_TASK_COMMANDS)
        for command in commands:
            # 判断系统支不支持该类型的type
            if command.get("type") not in COMMAND_TO_CLASS:
                return TurnPlanValidateResult(False, ClarifyReason.INVALID_TASK_COMMANDS)
        # 开启的流程最多一个
        start_commands=[c for c in commands if c.get("type")=="start_flow"]
        if len(start_commands) > 1:
            return TurnPlanValidateResult(False, ClarifyReason.MULTIPLE_TASK_FLOWS)


        # 目标 flow 必须存在且已解锁（主链路门禁：未解锁 → FLOW_LOCKED 澄清，
        for start_command in start_commands:
            flow_id=start_command.get("flow_id","")
            flow=self.flow_list.get_flow_by_id(flow_id)
            if flow is None:
                return TurnPlanValidateResult(False, ClarifyReason.UNKNOWN_TASK_FLOW)

            # 判断当前flow是否已经解锁
            if not self.flow_gate.is_unlocked(state.completed_flows, flow_id):
                return TurnPlanValidateResult(False, ClarifyReason.FLOW_LOCKED, flow_id)

        return TurnPlanValidateResult(True)

    def _knowledge(self, turn_plan, state):
        intents = turn_plan.knowledge.intents
        if not intents:
            return TurnPlanValidateResult(False, ClarifyReason.MISSING_TRACK)
        for intent in intents:
            # 和 command.get("type") not in COMMAND_TO_CLASS 同一种拦法；
            # 大模型偶尔会回成旧形状 ["ask_company_info"]，所以先判是不是 dict
            if not isinstance(intent, dict) or intent.get("intent") not in KNOWLEDGE_INTENTS:
                return TurnPlanValidateResult(False, ClarifyReason.MISSING_TRACK)
            need = KNOWLEDGE_INTENTS[intent["intent"]].requires_object
            if need and not (intent.get("objects") or {}).get(need):
                return TurnPlanValidateResult(False, ClarifyReason.MISSING_FOCUSED_OBJECT)
        return TurnPlanValidateResult(True)




