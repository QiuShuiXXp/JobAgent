"""业务轨道处理器：命令应用 → 流程推进 → 动作分派（注册表并入本类）。"""
import logging

from jobagent.api.errors import AppError, ErrorCode
from jobagent.domain.messages import BotMessage
from jobagent.domain.state import DialogueState
from jobagent.task.action.base import Action, ActionCall, ActionResult
from jobagent.task.command.processor import CommandProcessor
from jobagent.task.flow.executor import FlowExecutor
from jobagent.task.flow.flows import FlowsList

logger = logging.getLogger("jobagent.task")


class TaskHandler:

    def __init__(
            self,
            executor: FlowExecutor,
            processor: CommandProcessor,
            flow_list: FlowsList,
            actions: dict[str, Action],
    ):
        self.executor = executor
        self.processor = processor
        self.flow_list = flow_list
        self.actions = actions

    async def handle(self, command: list, state: DialogueState) -> list[BotMessage]:
        # 命令先行：开/恢复/取消流程、写槽位（状态机变更全部收敛在此）
        self.processor.run(commands=command, state=state, flow_list=self.flow_list)
        # 推进流程；推进器遇到 action 节点回调本类 run() 分派
        return await self.executor.execute_flow(state, self.flow_list, self)

    async def run(self, action_call: ActionCall, state: DialogueState) -> ActionResult:
        action = self.actions.get(action_call.name)
        if action is None:
            # 未注册动作：结构化错误而非裸吞
            logger.error("未知动作：%s", action_call.name)
            raise AppError(ErrorCode.INTERNAL_ERROR, f"unknown action {action_call.name}")
        return await action.run(state, action_call.args)