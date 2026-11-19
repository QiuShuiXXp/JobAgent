from dataclasses import asdict

from jinja2 import Template

from jobagent.domain.contexts import CollectedSystemContext
from jobagent.domain.messages import BotMessage
from jobagent.domain.state import DialogueState
from jobagent.task.action.base import Action, ActionResult


class ActionResponse(Action):

    name = "action_response"

    async def run(self,state: DialogueState,action_args:dict)->ActionResult:
        mode=action_args.get("mode","static")
        if mode=="rephrase":
            return await self._rephrase(state, action_args)
        else:
            return self._static(state, action_args)

    async def _rephrase(self, state, action_args):

        return self._static(state, action_args)

    def _static(self, state, action_args)->ActionResult:
       text=action_args.get("text")
       # 与流程条件/首问同一份槽位视图，系统过场上下文（无 slots）不会崩在这里
       rendered=Template(text).render(slots=state.slots_view,
                                      context=asdict(state.active_system_task) if state.active_system_task else {})
       # collect 首问带上 YAML 里配的快捷回复按钮；其余场合（过场白/业务话术）不带
       choices = []
       if isinstance(state.active_system_task, CollectedSystemContext):
           choices = (state.active_system_task.response or {}).get("choices") or []
       return ActionResult(messages=[BotMessage(text=rendered, choices=choices)])