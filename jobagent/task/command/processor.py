"""CommandProcessor：逐命令应用到 state，含中断栈与系统播报激活。"""
from typing import Any

from jobagent.domain.contexts import FLOW_ID_TO_CONTEXT_CLASS, TaskContext
from jobagent.domain.messages import BotMessage, FocusedObject, MessageType
from jobagent.domain.state import DialogueState
from jobagent.task.command.commands import StartedFlowCommand, ResumedFlowCommand, CancelFlowCommand, SetSlotsCommand
from jobagent.task.flow.flows import FlowsList
from jobagent.task.flow.steps import CollectFlowStep


class CommandProcessor:

    def run(self, state: DialogueState, commands: list, flow_list: FlowsList):
        # 本轮 set_slots 写入视图：新开流程播种用（顺序无关，set_slots 在 start_flow 前后都算）
        turn_writes: dict[str, Any] = {}
        for command in commands:
            if isinstance(command, StartedFlowCommand):
                self.start_flow(state, command, flow_list, turn_writes)
            elif isinstance(command, ResumedFlowCommand):
                self.resume_flow(state, command, flow_list)
            elif isinstance(command, CancelFlowCommand):
                self.cancel_flow(state, flow_list)
            elif isinstance(command, SetSlotsCommand):
                self.set_slots(state, command, turn_writes)

    def _announce(self, state: DialogueState, flow_list: FlowsList,
                  flow_id: str, **fields) -> None:
        """激活系统过场流程：按 flow_id 找上下文类，补上起始步骤，其余字段由调用方给。"""
        ctx_cls = FLOW_ID_TO_CONTEXT_CLASS[flow_id]
        state.start_system_task(ctx_cls(
            flow_id=flow_id,
            step_id=flow_list.get_flow_by_id(flow_id).get_start_step().id,
            **fields))

    def _seed_slots(self, state: DialogueState, turn_writes: dict[str, Any]) -> dict[str, Any]:
        """新开流程的播种槽：全局记忆打底 + 本轮写入覆盖。

        null 是规划器表达"这槽要重问/用户要改"的方式，
        播种时剔除，collect 见空才会重新首问；其余槽沿用旧值，改意向不用重答三遍。
        """
        merged = {**state.global_slots, **turn_writes}
        return {k: v for k, v in merged.items() if v is not None}

    def start_flow(self, state: DialogueState, command: StartedFlowCommand,
                   flow_list: FlowsList, turn_writes: dict[str, Any]):

        start_flow_id = command.flow_id
        start_flow = flow_list.get_flow_by_id(start_flow_id)

        active_task = state.active_task

        if active_task is not None:
            # 情况一 有当前正在执行的任务

            # 如果当前正在执行的流程刚好是要开启的流程
            if active_task.flow_id == start_flow_id:
                # 窗口类流程（restart_on_reentry）要重弹登录窗口，其余原地等
                self._replay_from_start(state, flow_list, start_flow_id)
                return

            # 如果不是，就中断当前流程，开启新流程 开启之前去栈中查找一次,如果栈中有就恢复，否则就开启

            # 中断当前任务
            state.interrupt_active_task()

            # 如果栈中没有就开启，有就恢复（这个方法一调用就会恢复）
            resume_flow = state.resume_task(flow_id=start_flow_id)
            if not resume_flow:
                state.start_task(self._new_task(state, start_flow, turn_writes))
            else:
                self._replay_from_start(state, flow_list, start_flow_id)

            # 如果恢复成功，需要激活中断系统流程：过场白，告诉用户我中断哪一个业务流程
            self._announce(state, flow_list, "system_task_interrupted",
                           interrupted_flow_id=active_task.flow_id,
                           interrupted_flow_name=flow_list.get_flow_by_id(active_task.flow_id).flow_name,
                           started_flow_id=start_flow_id,
                           started_flow_name=start_flow.flow_name)
        else:
            # 情况二 没有正在执行的任务

            # 先从栈中恢复一次
            resume_flow = state.resume_task(flow_id=start_flow_id)
            # 如果恢复成功进行系统恢复流程播报
            if resume_flow:
                self._replay_from_start(state, flow_list, start_flow_id)
                self._announce(state, flow_list, "system_task_resumed",
                               resumable_flow_id=start_flow_id,
                               resumable_flow_name=start_flow.flow_name)
            else:
                # 否则就正常开启业务流程
                state.start_task(self._new_task(state, start_flow, turn_writes))
                # 激活系统流程
                self._announce(state, flow_list, "system_task_started",
                               start_flow_id=start_flow_id,
                               start_flow_name=start_flow.flow_name)

    def _new_task(self, state: DialogueState, flow, turn_writes: dict[str, Any]):
        return TaskContext(flow_id=flow.flow_id,
                           step_id=flow.get_start_step().id,
                           slots=self._seed_slots(state, turn_writes))

    def cancel_flow(self, state, flow_list):
        biz = state.active_task
        if biz is None:
            # 没有业务流程在跑，没什么可取消的
            return
        cancel_flow_id = biz.flow_id
        cancel_flow = flow_list.get_flow_by_id(cancel_flow_id)

        # 取消 = 丢弃这轮没走完的修改，不并回全局记忆
        state.discard_task()

        # 激活系统流程过场白
        self._announce(state, flow_list, "system_task_canceled",
                       canceled_flow_id=cancel_flow_id,
                       canceled_flow_name=cancel_flow.flow_name)

        # 登录流程被取消：把等待中的登录卡片翻篇成「已取消」。不翻篇的话前端会一直
        # 轮询登录态，BOSS 左栏的折叠按钮也被「登录中禁用」锁死
        if cancel_flow_id == "boss_login":
            state.pending_turn.bot_messages.append(BotMessage(
                type=MessageType.OBJECT,
                object=FocusedObject(type="login_window", id="login_window",
                                     payload={"status": "canceled"})))

    def resume_flow(self, state, command, flow_list):
        """恢复被中断的业务流程；flow_id 为空时恢复栈顶。"""
        resume_flow_id = command.flow_id
        active_task = state.active_task

        if resume_flow_id is None:
            if not state.interrupted_active_tasks:
                return
            resume_flow_id = state.interrupted_active_tasks[-1].flow_id

        if active_task is not None:
            if active_task.flow_id == resume_flow_id:
                return
            interrupt_flow_id = active_task.flow_id
            interrupt_flow_name = flow_list.get_flow_by_id(interrupt_flow_id).flow_name
            state.interrupt_active_task()
            if not state.resume_task(flow_id=resume_flow_id):
                # 目标不在栈里：把刚中断的任务恢复回去，别让用户没有活跃流程
                state.resume_task(flow_id=interrupt_flow_id)
                return
            self._replay_from_start(state, flow_list, resume_flow_id)
            self._announce(state, flow_list, "system_task_interrupted",
                           interrupted_flow_id=interrupt_flow_id,
                           interrupted_flow_name=interrupt_flow_name,
                           started_flow_id=resume_flow_id,
                           started_flow_name=flow_list.get_flow_by_id(resume_flow_id).flow_name)
            return

        if not state.resume_task(flow_id=resume_flow_id):
            return
        self._replay_from_start(state, flow_list, resume_flow_id)
        self._announce(state, flow_list, "system_task_resumed",
                       resumable_flow_id=resume_flow_id,
                       resumable_flow_name=flow_list.get_flow_by_id(resume_flow_id).flow_name)

    def set_slots(self, state, command, turn_writes: dict[str, Any]):
        for key, value in command.slots.items():
            turn_writes[key] = value
            if value is None:
                # null = 清空重问：活跃任务、被中断任务与全局记忆一起抹掉
                state.drop_slots(key)
            else:
                state.set_slots({key: value})

    def _replay_from_start(self, state: DialogueState, flow_list: FlowsList, flow_id: str) -> None:
        """重进/恢复「重开窗」流程（flow 上标了 restart_on_reentry）时回到 start 重走：
        登录页多半已经不在人面前了，停在 collect 节点只会对着一个看不见的页面把
        「登录好了叫我」再念一遍。其余槽位不动（intent_confirmed 还在），重走的正是门禁 + 开窗这两步。
        """
        task = state.active_task
        if task is None or task.flow_id != flow_id:
            return
        flow = flow_list.get_flow_by_id(flow_id)
        if flow is None or not flow.restart_on_reentry:
            return
        parked = flow.get_step_by_id(task.step_id)
        task.step_id = flow.get_start_step().id
        # 挂着的收集过场属于上一轮那张卡，不抹掉会先把旧问句播一遍
        state.active_system_task = None
        # 旧卡片留下的自动确认一起作废：留着会从开窗直接跳去校验，页面还没展开就判失败
        if isinstance(parked, CollectFlowStep):
            state.drop_slots(parked.slot_name)