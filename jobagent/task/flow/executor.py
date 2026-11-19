from dataclasses import asdict
from typing import TYPE_CHECKING

from simpleeval import simple_eval

from jobagent.api.errors import AppError, ErrorCode
from jobagent.domain.contexts import CollectedSystemContext
from jobagent.domain.messages import BotMessage
from jobagent.domain.state import DialogueState
from jobagent.task.action.base import ActionCall, ActionResult
from jobagent.task.flow.flows import FlowsList
from jobagent.task.flow.links import StaticLink, ConditionLink, FallbackLink
from jobagent.task.flow.steps import (
    ActionFlowStep, CollectFlowStep, EndFlowStep, FlowStep, StartFlowStep,
)

if TYPE_CHECKING:
    from jobagent.task.handler import TaskHandler


class FlowExecutor:
    async def execute_flow(self,
                           state: DialogueState,
                           flow_list: FlowsList,
                           action_runner: "TaskHandler") -> list[BotMessage]:
        """
          流程推进器
          职责：推进yaml流程【业务流程、系统流程】，如果遇到流程的step类型action且名字action_listen
          （system_collect_information）,此时流程推进器就不能继续往下推
        """
        final_messages = []
        while True:  # 负责:流程的步骤类型是action类型
            # 1. 推进流程，找到流程的某一步类型是action
            action_call: ActionCall = self._advance_flow_util_action(state, flow_list)

            # 2. 判断action
            if action_call.name == "action_listen":
                break
            else:
                # 两种类型：a) action_response 响应   b) action_xxx 调用第三方接口
                action_result: ActionResult = await action_runner.run(action_call, state)
                final_messages.extend(action_result.messages)
                state.set_slots(action_result.slot_updates)

        # 3. 收集到的返回出去
        return final_messages

    def _advance_flow_util_action(self,
                                  state: DialogueState,
                                  flow_list: FlowsList) -> ActionCall:
        """
        推进流程：执行流程中的step
        职责：负责管step
        """
        while True:

            # 1. 获取当前正在执行的流程(上下文)(系统流程、业务流程)
            current_activating_task = state.current_task()

            if current_activating_task is None:
                return ActionCall(name="action_listen")

            # 2. 从上下文中获取正在执行的流程ID
            flow_id = current_activating_task.flow_id

            # 3. 根据当前流程的流程ID 获取流程对象
            flow = flow_list.get_flow_by_id(flow_id)

            # 4. 获取流程的步骤对象
            step = flow.get_step_by_id(current_activating_task.step_id)

            action_call: ActionCall | None = self._run_step(state, step, flow_list)

            # 5. action_call 返回
            if action_call is not None:
                return action_call

    def _run_step(self,
                  state: DialogueState,
                  step: FlowStep,
                  flow_list: FlowsList) -> ActionCall | None:
        """
        运行流程的某一步，step类型有4种：start、action、collect、end
        """
        if isinstance(step, StartFlowStep):
            return self._run_start_step(state, step)
        elif isinstance(step, EndFlowStep):
            return self._run_end_step(state)
        elif isinstance(step, ActionFlowStep):
            return self._run_action_step(state, step)
        elif isinstance(step, CollectFlowStep):
            return self._run_collect_step(state, step, flow_list)
        else:
            pass

    def _run_start_step(self,
                        state: DialogueState,
                        step: StartFlowStep) -> None:
        """职责：更新step_id即可（state.current_task().step_id）没有业务逻辑。"""
        self._advance_flow_step(state, step)
        return None

    def _advance_flow_step(self, state: DialogueState, step: FlowStep):
        """找下一个 step_id 并写回当前上下文，内层循环自动衔接下一步。"""
        step_id: str = self._select_step_id(state, step)
        state.current_task().step_id = step_id

    def _select_step_id(self, state: DialogueState, step: FlowStep) -> str:
        """找下一个 step_id：静态边 → 条件边（首个为真）→ 回退边。

        回退边最后才看，与 YAML 书写顺序无关；一条都走不通就响亮失败，
        绝不返回假 id——那会让推进器拿着 None step 无限空转。
        """
        fallback: str | None = None
        for link in step.next:
            if isinstance(link, StaticLink):
                return link.target
            if isinstance(link, ConditionLink):
                if self._eval_condition(state, link.condition):
                    return link.target
            if isinstance(link, FallbackLink):
                fallback = link.target
        if fallback is not None:
            return fallback
        raise AppError(ErrorCode.INTERNAL_ERROR,
                       f"流程步骤 {step.id} 没有可走的下一步（条件全不满足且无回退边）")

    def _eval_condition(self,
                        state: DialogueState,
                        condition: str) -> bool:
        """计算条件（边条件 / 槽位条件）：simpleeval 沙箱求值，不用原生 eval。

        槽位用 slots_view（全局 → 中断任务 → 活跃任务），与规划器同一份视图；
        纯系统流程（无活跃业务任务）时也能安全求值。
        """
        data = {
            "slots": state.slots_view,
            "context": asdict(state.active_system_task) if state.active_system_task else {},
        }
        return bool(simple_eval(condition, names=data, functions={"len": len}))

    def _run_end_step(self, state: DialogueState) -> None:
        """判断当前系统流程是否执行完 以及业务流程是否执行完。"""
        if state.active_system_task:
            state.end_system_task(state.active_system_task)
        elif state.active_task:
            state.end_task()

        return None

    def _run_action_step(self,
                         state: DialogueState,
                         step: ActionFlowStep) -> ActionCall:
        # 1. 更新下一步的step_id
        self._advance_flow_step(state, step)

        # 2. 构建action_call
        return self._build_action_call(state, step)

    def _build_action_call(self, state, step):
        # 1. 获取action_name
        action_name = step.action

        # 2. action_args："context.response" 式引用从系统上下文取值
        action_kwargs = step.args
        if isinstance(action_kwargs, str):
            action_kwargs = asdict(state.active_system_task)[action_kwargs.split(".")[1]]
        return ActionCall(name=action_name, args=action_kwargs)

    def _run_collect_step(self,
                          state: DialogueState,
                          step: CollectFlowStep,
                          flow_list: FlowsList):
        """
        注意：进入两次:第一次做完一定要返回None(内部循环才能继续推进下一步) 且不能更新step_id
        1. 第一次触发：system_collect_information系统流程进行槽位收集
        2. 第二次触发：对填写槽位的校验
        """
        self._try_to_fill_slot_from_focused_object(state, step)
        if state.active_task.slots.get(step.slot_name):
            # 第二次触发(槽位信息填写过：LLM/点击卡片)
            if step.validation:
                if self._eval_condition(state, step.validation.condition):
                    self._advance_flow_step(state, step)
                    return None
                else:
                    state.remove_slot(step.slot_name)
                    if step.validation.failure_response:
                        return ActionCall(name="action_response",
                                          args=asdict(step.validation.failure_response))
                    else:
                        return ActionCall(name="action_response",
                                          args={"text": "你填写的信息的有误，请您重新输入!"})
            else:
                self._advance_flow_step(state, step)
                return None
        else:
            # 第一次触发(激活收集信息的系统流程 等用户填写槽位信息)
            state.start_system_task(CollectedSystemContext(
                flow_id="system_collect_information",
                step_id=flow_list.get_flow_by_id("system_collect_information").get_start_step().id,
                # "response": {"mode": "static", "text": "期望的岗位方向是什么？…", "prompt": None}
                response=asdict(step.response),
                slot_name=step.slot_name
            ))
            return None

    def _try_to_fill_slot_from_focused_object(self, state: DialogueState, step: CollectFlowStep):
        if state.focused_object is None:
            return
        value = state.focused_object.payload.get(step.slot_name)
        if value is not None:
            state.set_slots({step.slot_name: value})
            state.focused_object = None
