from assistant_core.models import AssistantContext, BrainGraph, ConversationHistory, UserProfile


class ContextBuilder:
    """Assembles model-facing context from memory and app state."""

    def __init__(self, proactivity=None, file_evidence=None):
        self.proactivity = proactivity
        self.file_evidence = file_evidence

    def build(
        self,
        *,
        user_id: str,
        conversation_id: str,
        profile: UserProfile,
        history: ConversationHistory,
        graph: BrainGraph,
        graph_summary: list[str] | None = None,
        query: str = "",
    ) -> AssistantContext:
        relevant_connectors = [
            node.label for node in graph.nodes if node.kind == "connector"
        ]
        resolved_graph_summary = graph_summary if graph_summary is not None else []
        return AssistantContext(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            recent_messages=history.messages[-6:],
            relevant_connectors=relevant_connectors,
            graph_summary=resolved_graph_summary,
            active_goals=[goal["title"] for goal in self.proactivity.handle({})["goals"][:3]]
            if self.proactivity is not None and user_id == "local-user" else [],
            file_evidence=self.file_evidence.retrieve(query)
            if self.file_evidence is not None and user_id == "local-user" and query else [],
        )
