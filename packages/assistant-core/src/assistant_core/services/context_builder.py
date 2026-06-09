from assistant_core.models import AssistantContext, BrainGraph, ConversationHistory, UserProfile


class ContextBuilder:
    """Assembles model-facing context from memory and app state."""

    def build(
        self,
        *,
        user_id: str,
        conversation_id: str,
        profile: UserProfile,
        history: ConversationHistory,
        graph: BrainGraph,
        graph_summary: list[str] | None = None,
    ) -> AssistantContext:
        relevant_connectors = [
            node.label for node in graph.nodes if node.kind == "connector"
        ]
        resolved_graph_summary = graph_summary or [
            f"{edge.source} {edge.relation} {edge.target}"
            for edge in graph.edges[:8]
        ]
        return AssistantContext(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            recent_messages=history.messages[-6:],
            relevant_connectors=relevant_connectors,
            graph_summary=resolved_graph_summary,
        )
