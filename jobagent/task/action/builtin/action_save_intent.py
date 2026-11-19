from typing import Any

from jobagent.api.errors import AppError, ErrorCode
from jobagent.domain.messages import BotMessage
from jobagent.domain.state import DialogueState
from jobagent.task.action.base import Action, ActionResult


class ActionSaveIntent(Action):
    """
    用于保存用户的意向城市和岗位
    """
    name = "action_save_intent"

    async def run(self,state:DialogueState,args:dict[str,Any]):
        # 获取城市槽位
        intent_position=state.get_slots("intent_position")
        # 获取岗位槽位
        intent_cities=state.get_slots("intent_cities")

        if not intent_position or not intent_cities:
            raise AppError(ErrorCode.INTENT_FIELD_MISSING,message="岗位方向或目标城市尚未收集完整")
        # 薪水 可选
        salary_raw = state.get_slots("intent_salary")
        # 把城市转为列表
        cities = [c.strip() for c in str(intent_cities).replace("，", ",").split(",") if c.strip()]

        # 意向以槽位为准（global_slots 跨流程保留），确认标记供门禁用
        state.set_slots({"intent_confirmed": "yes"})
        salary_part = f"，期望薪资 {salary_raw}" if salary_raw else ""
        return ActionResult(
            messages=[BotMessage(
            text=f"好的，已记录你的求职意向：{intent_position}（{'、'.join(cities)}{salary_part}）。"
                 f"接下来可以上传简历，或说「登录」去 BOSS直聘筛岗位。")]
        )
