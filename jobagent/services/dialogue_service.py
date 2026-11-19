"""对话服务：加载状态 → 引擎处理 → 保存状态（整读整写，无冲突重试）。"""
import logging

from jobagent.domain.messages import UserMessage
from jobagent.engine.dialogue_engine import DialogueEngine
from jobagent.infrastructure.log_config import bind_user
from jobagent.repository.dialogue_repository import DialogueRepository

logger = logging.getLogger("jobagent.dialogue_service")


class DialogueService:
    def __init__(self, repository: DialogueRepository, engine:DialogueEngine) -> None:
        self.repository = repository
        self.engine = engine

    async def handle_chat(self, sender_id: str, user_message: UserMessage):
        bind_user(sender_id)
        # 加载
        state = await self.repository.load_dialogue(sender_id)
        # 引擎处理（纯内存计算，就state）
        result = await self.engine.handle_message(user_message, state)
        # 保存状态
        await self.repository.save_dialogue(state)
        return result