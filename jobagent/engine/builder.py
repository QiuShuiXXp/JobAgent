from jobagent.config.paths import ASSETS_ROOT

from jobagent.engine.clarify import ClarifyResponser
from jobagent.config.settings import settings
from jobagent.domain.state import DialogueState
from jobagent.engine.dialogue_engine import DialogueEngine
from jobagent.history.summarizer import SessionSummarizer
from jobagent.history.window import HistoryWindow
from jobagent.knowledge.handler import KnowledgeHandler
from jobagent.knowledge.intents import KNOWLEDGE_INTENTS
from jobagent.knowledge.providers.knowledge import CompanyInformationMcpProvider
from jobagent.knowledge.responder import KnowledgeResponder
from jobagent.plan.planner import TurnPlanner
from jobagent.plan.validator import TurnPlanValidator
from jobagent.prompts.loader import PromptLoader
from jobagent.services.logout_service import LOGIN_SLOTS, LOGIN_FLOWS, LogoutService
from jobagent.task.action.builder import build_actions
from jobagent.task.command.processor import CommandProcessor
from jobagent.task.flow.executor import FlowExecutor
from jobagent.task.flow.flows import FlowsList
from jobagent.task.flow.gates import FlowGate
from jobagent.task.handler import TaskHandler
from jobagent.task.loader import FlowLoader

PROJECT_ROOT = ASSETS_ROOT

def build_engine(resume_service=None, browser_manager=None,
                 match_service=None, task_manager=None) -> DialogueEngine:
    # 加载流程
    flow_list: FlowsList = FlowLoader().load_many([
        PROJECT_ROOT / "flow_config" / "user_flows.yml",
        PROJECT_ROOT / "flow_config" / "system_flows.yml"])
    flow_gate = FlowGate.from_flows(flow_list)   # DAG 门禁（流程依赖图）
    logout_service = LogoutService(browser_manager) if browser_manager is not None else None
    # 动作注册表：依赖哪个服务就注册哪个动作
    actions = build_actions(resume_service, browser_manager,
                            match_service, task_manager, logout_service)
    loader = PromptLoader()
    summarizer = SessionSummarizer(loader)
    planner = TurnPlanner(
        template_text=loader.raw("turn_plan.j2"),
        summary_lookup=summarizer.latest_context,   # 情景记忆入口；删掉这行 = 退回无摘要
        window=HistoryWindow.from_settings(),       # 工作记忆窗口（轮数+字符预算）
        flow_gate=flow_gate,                          # DAG 门禁
    )
    #  校验器 / 澄清 / 处理器
    validator = TurnPlanValidator(flow_list, flow_gate=flow_gate)
    clarify = ClarifyResponser(loader)
    task_handler = TaskHandler(
        processor=CommandProcessor(),
        executor=FlowExecutor(),
        flow_list=flow_list,
        actions=actions,
    )
    # 知识库轨道：目前只有一个联网搜索 provider，直接按 id 建表
    knowledge_handler = KnowledgeHandler(
        providers={"mcp.websearch": CompanyInformationMcpProvider()},
        intents=KNOWLEDGE_INTENTS,
        knowledge_responder=KnowledgeResponder(loader),
    )

    return DialogueEngine(
        planner=planner, validator=validator, clarify=clarify,
        task_handler=task_handler,
        knowledge_handler=knowledge_handler,
        flow_gate=flow_gate,
        session_timeout_minutes=settings.session_timeout_minutes,
        on_session_closed=build_session_closed_hook(logout_service, summarizer),
        on_turn_committed=summarizer.maybe_fold,   # 滚动折叠：每轮提交后够一批就折
    )


def build_session_closed_hook(logout_service: LogoutService | None,
                              summarizer: SessionSummarizer | None):
    """
    会话过期：退出登录（登录记录不跨会话）+ 折剩余尾部（情景记忆）。

    退出登录要 await——它得在用户下一条消息之前真的生效。
    折叠整个扔后台：同步派任务，请求链路一秒都不多等。
    """
    async def on_session_closed(state: DialogueState) -> None:
        if logout_service is not None:
            await logout_service.logout(state.sender_id)
            state.drop_slots(*LOGIN_SLOTS)
            state.completed_flows.difference_update(LOGIN_FLOWS)
        if summarizer is not None:
            summarizer.close_session(state)
    return on_session_closed