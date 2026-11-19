from dataclasses import dataclass, field


@dataclass
class KnowledgeIntent:
    description: str
    provider_ids: list[str] = field(default_factory=list)
    requires_object: str | None = None
    object_hint: str = ""

KNOWLEDGE_INTENTS:dict[str,KnowledgeIntent]={
    "ask_company_info": KnowledgeIntent(
        description="查一家公司的公开信息",
        provider_ids=["mcp.websearch"],
        requires_object="search_query",
        object_hint="一句检索词，不超过 20 字：主体名 + 想查的方面"),
}