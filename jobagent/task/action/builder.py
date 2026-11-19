"""动作装配：按注入的服务决定注册哪些动作，重名 fail-fast。"""
from jobagent.task.action.base import Action
from jobagent.task.action.builtin.action_listen import ActionListen
from jobagent.task.action.builtin.action_response import ActionResponse
from jobagent.task.action.builtin.action_save_intent import ActionSaveIntent
from jobagent.task.action.builtin.action_save_resume import ActionParseResume
from jobagent.task.action.customer.action_login import ActionConfirmLogin, ActionOpenLogin
from jobagent.task.action.customer.action_logout import ActionLogout
from jobagent.task.action.customer.action_search_jobs import ActionSearchJobs
from jobagent.task.action.customer.action_send_greet import ActionSendGreet


def _register(actions: dict[str, Action], action: Action) -> None:
    if action.name in actions:      # 重名 fail-fast（配置冲突启动期暴露）
        raise ValueError(f"duplicate action name: {action.name}")
    actions[action.name] = action


def build_actions(resume_service=None, browser_manager=None,
                  match_service=None, task_manager=None,
                  logout_service=None) -> dict[str, Action]:
    actions: dict[str, Action] = {}
    # 内置动作
    _register(actions, ActionResponse())
    _register(actions, ActionListen())
    if resume_service is not None:
        _register(actions, ActionParseResume(resume_service))
    _register(actions, ActionSaveIntent())
    # 业务动作：依赖哪个服务就注册哪个
    if browser_manager is not None:
        _register(actions, ActionOpenLogin(browser_manager))
        _register(actions, ActionConfirmLogin(browser_manager))
    if browser_manager is not None and match_service is not None:
        _register(actions, ActionSearchJobs(browser_manager, match_service))
    if task_manager is not None:
        _register(actions, ActionSendGreet(task_manager))
    if logout_service is not None:
        _register(actions, ActionLogout(logout_service))
    return actions