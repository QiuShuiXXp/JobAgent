from jobagent.domain.messages import Turn, BotMessage
from jobagent.history.builder import ChatHistoryBuilder
from jobagent.infrastructure.llm_client import get_llm
from jobagent.knowledge.providers.base import KnowledgeChunk
from jobagent.prompts.loader import PromptLoader

NOT_FOUND = "没查到相关的公开信息，换个说法或者把要查的对象说具体点再试试。"
class KnowledgeResponder:
    def __init__(self, loader: PromptLoader) -> None:
        self.loader = loader
    async def respond(self, user_message, chunks: list[KnowledgeChunk], recent_turns: list[Turn]):
        knowledge_content = "\n\n".join([chunk.content for chunk in chunks if chunk.content])
        # 空材料必须在渲染之前拦掉：一旦进了 LLM，它就会拿"资料是空的"编出一段介绍
        if not knowledge_content:
            return [BotMessage(text=NOT_FOUND)]

        prompt = self.loader.render(
            "knowledge_answer.j2",
            history=ChatHistoryBuilder.build(recent_turns),
            user_message=user_message.text or "",
            knowledge_content=knowledge_content
        )

        reply = await get_llm().ainvoke(prompt)
        return [BotMessage(text=(reply.content or "").strip() or NOT_FOUND)]

