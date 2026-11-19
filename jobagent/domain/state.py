import time
import uuid
from dataclasses import dataclass, field
from typing import Any



from jobagent.domain.contexts import TaskContext, SystemContext
from jobagent.domain.messages import Turn, FocusedObject, UserMessage


@dataclass(slots=True)
class Session:
    session_id: str
    started_at: float
    last_activity_at: float
    closed_at: float | None = None
    turns: list[Turn] = field(default_factory=list)


    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id":self.session_id,
            "started_at":self.started_at,
            "last_activity_at":self.last_activity_at,
            "closed_at":self.closed_at,
            "turns":[turn.to_dict() for turn in self.turns]
        }

    @classmethod
    def from_dict(cls,data: dict[str, Any])->"Session":
        return cls(
            session_id=data["session_id"],
            started_at=data["started_at"],
            last_activity_at=data["last_activity_at"],
            closed_at=data.get("closed_at"),
            turns=[Turn.from_dict(t) for t in data.get("turns",[])]
        )


@dataclass(slots=True)
class DialogueState:
    sender_id: str  # 用户id
    active_task: TaskContext | None = None
    active_system_task: SystemContext | None = None  # 当前系统流程
    interrupted_active_tasks: list[TaskContext] = field(default_factory=list)
    focused_object: FocusedObject | None = None
    sessions: list[Session] = field(default_factory=list)  # 一个用户可能有多个会话
    current_session_id: Session | None = None  # 当前的会话
    pending_turn: Turn | None = None
    global_slots: dict[str, Any] = field(default_factory=dict)  # 跨流程保留的槽位（门禁用）
    completed_flows: set[str] = field(default_factory=set)  # 已结束（end 节点走过）的业务流程

    # ---------流程相关-----------

    # ------ 系统流程相关-----------

    # 开启系统流程
    def start_system_task(self, ctx: SystemContext) -> SystemContext:
        self.active_system_task = ctx
        return self.active_system_task

    # 关闭系统流程
    def end_system_task(self, ctx: SystemContext) -> None:
        self.active_system_task = None

    # 当前正在运行的流程 可能是系统流程 或者 业务流程
    def current_task(self) -> SystemContext | TaskContext | None:
        return self.active_system_task or self.active_task

    #  --------业务流程相关----------

    def start_task(self, task: TaskContext):
        self.active_task = task

    # 结束当前业务流程
    def end_task(self):
        task = self.active_task
        if task:
            self.global_slots.update(task.slots)
            self.completed_flows.add(task.flow_id)  # DAG 门禁：流程结束 = 完成
        self.active_task = None

    def discard_task(self):
        """取消流程：任务槽直接丢弃，不并回 global_slots。"""
        self.active_task = None

    # 中断当前业务流程
    def interrupt_active_task(self):
        if self.active_task is None:
            return
        self.interrupted_active_tasks.append(self.active_task)
        self.active_task = None

    # 恢复业务流程
    def resume_task(self, flow_id: str) -> bool:

        for task in self.interrupted_active_tasks:
            if task.flow_id==flow_id:
                self.active_task=task
                self.interrupted_active_tasks.remove(task)
                return True
        return False
    # --------槽位相关----------

    @property
    def slots_view(self) -> dict[str, Any]:
        """规划器、条件边、话术渲染共用的槽位视图。

        层次：全局 → 被中断的任务 → 当前活跃任务，后者覆盖前者。
        规划器（intent_slots_json）、条件边、话术渲染共用这份视图；
        DAG 门禁改用 completed_flows（流程依赖），不再读槽位视图。
        """
        slots: dict[str, Any] = dict(self.global_slots)
        for task in self.interrupted_active_tasks:
            slots.update(task.slots)
        task = self.active_task
        if task is not None:
            slots.update(task.slots)
        return slots


    # 设置槽位
    def set_slots(self,slots:dict[str,Any]):
        task = self.active_task
        if task is not None:
            task.slots.update(slots)
        else:
            self.global_slots.update(slots)

    # 获取槽位
    def get_slots(self,name: str, default: Any = None):
        task = self.active_task
        if task is not None:
            if name in task.slots:
                return task.slots[name]
        if name in self.global_slots:
            return self.global_slots[name]
        return default

    # 删除槽位
    def remove_slot(self, name: str) -> None:
        """collect 校验失败删槽：下一轮重新首问的关键。"""
        task = self.active_task
        if task is not None:
            task.slots.pop(name, None)

    def drop_slots(self, *names: str) -> None:
        """彻底抹掉槽位：活跃任务、被中断任务与全局记忆一起清。"""
        task = self.active_task
        for name in names:
            if task is not None:
                task.slots.pop(name, None)
            for parked in self.interrupted_active_tasks:
                parked.slots.pop(name, None)
            self.global_slots.pop(name, None)


    #  --------Session相关----------
    def start_session(self):
        now_time=time.time()
        session = Session(session_id=str(uuid.uuid4()), started_at=now_time, last_activity_at=now_time)
        self.sessions.append(session)
        self.current_session_id = session
        return session

    def current_session(self) -> Session | None:
        cur_id = self.current_session_id.session_id if isinstance(self.current_session_id, Session) else self.current_session_id
        if cur_id is None:
            return None
        for session in self.sessions:
            if session.session_id == cur_id:
                return session
        return None
    def last_closed_session(self):
        # 最近被关闭的会话
        closed=[s for s in self.sessions if s.closed_at is not None]
        return closed[-1] if closed else None
    def close_current_session(self):
        current_session=self.current_session()
        if current_session is not None:
            current_session.closed_at=time.time()
            self.current_session_id=None

    def reset_runtime_state_for_new_session(self) -> None:
        """新会话前的清理：只清运行时字段；不清 sessions（历史要保留）。"""
        self.active_task = None
        self.active_system_task = None
        self.focused_object = None
        self.interrupted_active_tasks = []
        self.completed_flows = set()

    def clear_history(self) -> int:
        """全部清空：会话、流程断点、全局槽位（意向/简历/登录态）。返回清掉的轮数。"""
        turns = sum(len(session.turns) for session in self.sessions)
        self.sessions = []
        self.current_session_id = None
        self.pending_turn = None
        self.active_task = None
        self.active_system_task = None
        self.interrupted_active_tasks = []
        self.focused_object = None
        self.global_slots = {}
        self.completed_flows = set()
        return turns

    # -----------Turn相关-------------
    def begin_turn(self,user_message: UserMessage):
        self.pending_turn=Turn(
            turn_id=str(uuid.uuid4()),
            user_message=user_message,
            bot_messages=[]
        )
    def commit_pending_turn(self):
        session = self.current_session()
        if self.pending_turn is not None and session is not None:

            session.turns.append(self.pending_turn)
            session.last_activity_at = time.time()
        self.pending_turn = None

    # 卡片对象
    def set_focused_object(self, obj: FocusedObject) -> None:
        self.focused_object = obj

        # ---- 序列化：整份进出（持久化前提）----

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_id": self.sender_id,
            "active_task": self.active_task.to_dict() if self.active_task else None,
            "interrupted_active_tasks": [t.to_dict() for t in self.interrupted_active_tasks],
            "active_system_task": (self.active_system_task.to_dict()
                                   if self.active_system_task else None),
            "focused_object": self.focused_object.to_dict() if self.focused_object else None,
            "sessions": [s.to_dict() for s in self.sessions],
            "current_session_id": (self.current_session_id.session_id
                                   if isinstance(self.current_session_id, Session) else None),
            "pending_turn": self.pending_turn.to_dict() if self.pending_turn else None,
            "global_slots": self.global_slots,
            "completed_flows": sorted(self.completed_flows),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DialogueState":
        sessions = [Session.from_dict(s) for s in data.get("sessions", [])]
        cur_id = data.get("current_session_id")
        current = None
        if isinstance(cur_id, str):          # 新格式：session_id 字符串
            current = next((s for s in sessions if s.session_id == cur_id), None)
        elif isinstance(cur_id, dict):       # 旧格式：整个 Session 的 dict
            current = next((s for s in sessions
                            if s.session_id == cur_id.get("session_id")), None)
        return cls(
            sender_id=data["sender_id"],
            active_task=(TaskContext.from_dict(data["active_task"])
                         if data.get("active_task") else None),
            interrupted_active_tasks=[TaskContext.from_dict(t) for t in data.get("interrupted_active_tasks", [])],
            active_system_task=(SystemContext.from_dict(data["active_system_task"])
                                if data.get("active_system_task") else None),
            focused_object=(FocusedObject.from_dict(data["focused_object"])
                            if data.get("focused_object") else None),
            sessions=sessions,
            current_session_id=current,
            pending_turn=(Turn.from_dict(data["pending_turn"])
                          if data.get("pending_turn") else None),
            global_slots=data.get("global_slots", {}),
            completed_flows=set(data.get("completed_flows", [])),
        )
