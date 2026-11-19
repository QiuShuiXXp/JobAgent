import json
from typing import Any

from jobagent.domain.messages import BotMessage, MessageType, FocusedObject
from jobagent.domain.state import DialogueState
from jobagent.task.action.base import Action, ActionResult


class ActionParseResume(Action):
    name = "action_parse_resume"

    """
    任务：调 resume_service 解析简历，产出摘要卡片 + 写槽位。
    """

    def __init__(self, resume_service):
        self.resume_service = resume_service

    async def run(self, state: DialogueState, args: dict[str, Any]):
        # 上传简历槽位校验
        file_ref = state.get_slots("resume_file_ref")
        if not file_ref:
            raise ValueError("未检测到简历文件，请先上传简历")

        # 解析：读文件 → 抽文本 → LLM 结构化，全部收敛在 resume_service 里
        summary = await self.resume_service.parse_file(str(file_ref))

        # 写槽位：摘要以 JSON 字符串存进对话状态
        state.set_slots({"resume_summary": json.dumps(summary, ensure_ascii=False)})

        # 返回摘要卡片给前端
        return ActionResult(
            messages=[BotMessage(
                type=MessageType.OBJECT,
                object=FocusedObject(type="resume", id=str(file_ref), payload=summary),
            )]
        )
