from jobagent.domain.messages import BotMessage
from jobagent.domain.state import DialogueState
from jobagent.history.window import HistoryWindow
from jobagent.knowledge.intents import KnowledgeIntent
from jobagent.knowledge.providers.base import KnowledgeQuery
from jobagent.knowledge.responder import KnowledgeResponder


class KnowledgeHandler:

    def __init__(self, providers: dict,
                 intents: dict[str, KnowledgeIntent],
                 knowledge_responder: KnowledgeResponder,
                 window: HistoryWindow | None = None
                 ):
        self.intents = intents
        self.knowledge_responder = knowledge_responder
        self.providers = providers
        self.window = window or HistoryWindow.from_settings()

    async def handle(self, state: DialogueState, queries: list[KnowledgeQuery]) -> list[BotMessage]:
        # 获取知识chunks
        chunks = await self._retrieve(queries, state)

        # 调用llm 润色
        session = state.current_session()
        return await self.knowledge_responder.respond(
            user_message=state.pending_turn.user_message,
            chunks=chunks,
            recent_turns=self.window.select(session.turns)
        )

    async def _retrieve(self, queries: list[KnowledgeQuery], state: DialogueState):
        chunks = []
        for query in queries:
            for provider_id in self.intents[query.intent].provider_ids:
                chunks.extend(await self.providers[provider_id].retrieve(query, state))
        return chunks


    
