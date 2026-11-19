import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jobagent.domain.state import DialogueState
from jobagent.model.state_record import DialogueStateRecord
from jobagent.repository.session_repository import SessionRepository


class DialogueRepository:
    def __init__(self,session:AsyncSession):
        self.session =session


    async def load_dialogue(self,sender_id:str):
        row = (await self.session.execute(
            select(DialogueStateRecord.state_json)
            .where(DialogueStateRecord.sender_id == sender_id))).one_or_none()
        if row is None:
            return DialogueState(sender_id=sender_id)
        else:
            data = row.state_json
            if isinstance(data, str):   # 兼容旧数据：json.dumps 后存入 JSON 列（双重编码）
                data = json.loads(data)
            return DialogueState.from_dict(data)

    async def save_dialogue(self,state:DialogueState)->None:
        # 取行再写：on_duplicate_key_update 是 MySQL 专有方言，桌面端的 SQLite 用不了
        row = await self.session.get(DialogueStateRecord, state.sender_id)
        if row is None:
            self.session.add(DialogueStateRecord(
                sender_id=state.sender_id,
                state_json=state.to_dict()))      # JSON 列直接存 dict（自动序列化，勿再 json.dumps）
        else:
            row.state_json = state.to_dict()
        await self.session.commit()

    async def clear_history(self, sender_id: str) -> dict[str, int]:
        """全部清空：对话历史、流程断点、全局槽位与折叠摘要。"""
        state = await self.load_dialogue(sender_id)
        turns = state.clear_history()
        await self.save_dialogue(state)
        summaries = await SessionRepository(self.session).delete_by_sender(sender_id)
        return {"cleared_turns": turns, "cleared_summaries": summaries}
