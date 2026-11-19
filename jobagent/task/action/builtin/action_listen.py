from typing import Any

from jobagent.domain.state import DialogueState
from jobagent.task.action.base import Action, ActionResult


class ActionListen(Action):
    name = "action_listen"

    async def run(self,state:DialogueState,args:dict[str,Any])->ActionResult:
        return ActionResult()