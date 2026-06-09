import json
from pathlib import Path

from assistant_core.models import (
    BrainGraph,
    BrainGraphEdge,
    BrainGraphNode,
    ConversationHistory,
    ConversationMessage,
    ImportHistoryEntry,
    UserPreference,
    UserProfile,
    UserWorkflow,
)


def _default_graph() -> BrainGraph:
    return BrainGraph(
        nodes=[
            BrainGraphNode(
                id="assistant:cleo",
                label="Cleo",
                kind="assistant",
                group="core",
                metadata={"role": "orchestrator"},
            ),
            BrainGraphNode(
                id="memory:profile",
                label="Profile Memory",
                kind="memory",
                group="core",
                metadata={"scope": "user preferences"},
            ),
            BrainGraphNode(
                id="memory:tasks",
                label="Task Memory",
                kind="memory",
                group="core",
                metadata={"scope": "open tasks and plans"},
            ),
            BrainGraphNode(
                id="surface:mobile",
                label="Mobile App",
                kind="surface",
                group="clients",
                metadata={"priority": "primary"},
            ),
            BrainGraphNode(
                id="surface:terminal",
                label="Terminal CLI",
                kind="surface",
                group="clients",
                metadata={"priority": "power user"},
            ),
            BrainGraphNode(
                id="surface:browser",
                label="Browser Companion",
                kind="surface",
                group="clients",
                metadata={"priority": "secondary"},
            ),
            BrainGraphNode(
                id="connector:google",
                label="Google Workspace",
                kind="connector",
                group="integrations",
                metadata={"apps": "Gmail, Calendar, Drive"},
            ),
            BrainGraphNode(
                id="connector:notion",
                label="Notion",
                kind="connector",
                group="integrations",
                metadata={"apps": "Notes, wiki, projects"},
            ),
            BrainGraphNode(
                id="connector:github",
                label="GitHub",
                kind="connector",
                group="integrations",
                metadata={"apps": "Repos, issues, PRs"},
            ),
            BrainGraphNode(
                id="connector:filesystem",
                label="Filesystem",
                kind="connector",
                group="integrations",
                metadata={"apps": "Local files and notes"},
            ),
        ],
        edges=[
            BrainGraphEdge(source="assistant:cleo", target="memory:profile", relation="uses"),
            BrainGraphEdge(source="assistant:cleo", target="memory:tasks", relation="uses"),
            BrainGraphEdge(source="assistant:cleo", target="surface:mobile", relation="serves"),
            BrainGraphEdge(source="assistant:cleo", target="surface:terminal", relation="serves"),
            BrainGraphEdge(source="assistant:cleo", target="surface:browser", relation="serves"),
            BrainGraphEdge(source="assistant:cleo", target="connector:google", relation="connects"),
            BrainGraphEdge(source="assistant:cleo", target="connector:notion", relation="connects"),
            BrainGraphEdge(source="assistant:cleo", target="connector:github", relation="connects"),
            BrainGraphEdge(source="assistant:cleo", target="connector:filesystem", relation="connects"),
            BrainGraphEdge(source="memory:tasks", target="connector:notion", relation="syncs_with", strength=0.7),
            BrainGraphEdge(source="memory:profile", target="connector:google", relation="personalizes", strength=0.6),
        ],
    )


class PersistentStateBackend:
    """Tiny JSON-backed store for profiles, graph memory, and conversations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._state = {
            "profiles": {},
            "graph": _default_graph().model_dump(mode="json"),
            "conversations": {},
            "imports": [],
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(loaded, dict):
            return
        self._state["profiles"] = loaded.get("profiles", {}) or {}
        self._state["graph"] = loaded.get("graph", self._state["graph"]) or self._state["graph"]
        self._state["conversations"] = loaded.get("conversations", {}) or {}
        self._state["imports"] = loaded.get("imports", []) or []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(json.dumps(self._state, indent=2))
        temp_path.replace(self.path)

    def load_profiles(self) -> dict[str, UserProfile]:
        profiles: dict[str, UserProfile] = {}
        for user_id, payload in self._state.get("profiles", {}).items():
            profiles[user_id] = UserProfile.model_validate(payload)
        return profiles

    def save_profiles(self, profiles: dict[str, UserProfile]) -> None:
        self._state["profiles"] = {
            user_id: profile.model_dump(mode="json")
            for user_id, profile in profiles.items()
        }
        self.save()

    def load_graph(self) -> BrainGraph:
        return BrainGraph.model_validate(self._state.get("graph", _default_graph().model_dump(mode="json")))

    def save_graph(self, graph: BrainGraph) -> None:
        self._state["graph"] = graph.model_dump(mode="json")
        self.save()

    def load_conversations(self) -> dict[str, dict[str, list[ConversationMessage]]]:
        conversations: dict[str, dict[str, list[ConversationMessage]]] = {}
        for user_id, user_conversations in self._state.get("conversations", {}).items():
            conversations[user_id] = {}
            for conversation_id, messages in user_conversations.items():
                conversations[user_id][conversation_id] = [
                    ConversationMessage.model_validate(message)
                    for message in messages
                ]
        return conversations

    def save_conversations(
        self,
        conversations: dict[str, dict[str, list[ConversationMessage]]],
    ) -> None:
        self._state["conversations"] = {
            user_id: {
                conversation_id: [message.model_dump(mode="json") for message in messages]
                for conversation_id, messages in user_conversations.items()
            }
            for user_id, user_conversations in conversations.items()
        }
        self.save()

    def load_import_history(self) -> list[ImportHistoryEntry]:
        return [
            ImportHistoryEntry.model_validate(entry)
            for entry in self._state.get("imports", [])
        ]

    def save_import_history(self, entries: list[ImportHistoryEntry]) -> None:
        self._state["imports"] = [entry.model_dump(mode="json") for entry in entries]
        self.save()


class InMemoryProfileStore:
    """Tiny placeholder for user profile and preference memory."""

    def __init__(self, backend: PersistentStateBackend | None = None) -> None:
        self._backend = backend
        self._profiles: dict[str, UserProfile] = (
            backend.load_profiles() if backend else {}
        )

    def get_profile(self, user_id: str) -> UserProfile:
        return self._profiles.setdefault(user_id, UserProfile(user_id=user_id))

    def set_display_name(self, user_id: str, display_name: str) -> UserProfile:
        profile = self.get_profile(user_id)
        profile.display_name = display_name
        self._persist()
        return profile

    def set_preference(
        self,
        user_id: str,
        key: str,
        value: str,
        *,
        source: str = "manual",
    ) -> UserProfile:
        profile = self.get_profile(user_id)
        existing = next((item for item in profile.preferences if item.key == key), None)
        if existing:
            existing.value = value
            existing.source = source
        else:
            profile.preferences.append(UserPreference(key=key, value=value, source=source))
        self._persist()
        return profile

    def add_workflow(
        self,
        user_id: str,
        name: str,
        pattern: str,
        *,
        source: str = "inferred",
    ) -> UserProfile:
        profile = self.get_profile(user_id)
        existing = next((item for item in profile.workflows if item.name == name), None)
        if existing:
            existing.pattern = pattern
            existing.source = source
        else:
            profile.workflows.append(UserWorkflow(name=name, pattern=pattern, source=source))
        self._persist()
        return profile

    def _persist(self) -> None:
        if self._backend:
            self._backend.save_profiles(self._profiles)


class InMemoryBrainGraphStore:
    """Placeholder graph memory for visualizing assistant context and relationships."""

    def __init__(self, backend: PersistentStateBackend | None = None) -> None:
        self._backend = backend
        self._graph = backend.load_graph() if backend else _default_graph()

    def get_graph(self) -> BrainGraph:
        return self._graph

    def ensure_node(
        self,
        *,
        node_id: str,
        label: str,
        kind: str,
        group: str,
        metadata: dict[str, str] | None = None,
    ) -> BrainGraphNode:
        existing = next((node for node in self._graph.nodes if node.id == node_id), None)
        if existing:
            existing.label = label
            existing.kind = kind
            existing.group = group
            existing.metadata.update(metadata or {})
            self._persist()
            return existing
        node = BrainGraphNode(
            id=node_id,
            label=label,
            kind=kind,
            group=group,
            metadata=metadata or {},
        )
        self._graph.nodes.append(node)
        self._persist()
        return node

    def ensure_edge(
        self,
        *,
        source: str,
        target: str,
        relation: str,
        strength: float = 1.0,
    ) -> BrainGraphEdge:
        existing = next(
            (
                edge
                for edge in self._graph.edges
                if edge.source == source and edge.target == target and edge.relation == relation
            ),
            None,
        )
        if existing:
            existing.strength = strength
            self._persist()
            return existing
        edge = BrainGraphEdge(
            source=source,
            target=target,
            relation=relation,
            strength=strength,
        )
        self._graph.edges.append(edge)
        self._persist()
        return edge

    def sync_profile(self, profile: UserProfile) -> None:
        user_node_id = f"user:{profile.user_id}"
        self.ensure_node(
            node_id=user_node_id,
            label=profile.display_name or profile.user_id,
            kind="user",
            group="people",
            metadata={"display_name": profile.display_name or profile.user_id},
        )
        self.ensure_edge(
            source="assistant:cleo",
            target=user_node_id,
            relation="supports",
            strength=1.0,
        )
        for preference in profile.preferences:
            preference_node_id = f"preference:{profile.user_id}:{preference.key}"
            self.ensure_node(
                node_id=preference_node_id,
                label=preference.key.replace("_", " "),
                kind="preference",
                group="memory",
                metadata={
                    "value": preference.value,
                    "source": preference.source,
                },
            )
            self.ensure_edge(
                source=user_node_id,
                target=preference_node_id,
                relation="prefers",
                strength=0.9,
            )
        for workflow in profile.workflows:
            workflow_node_id = f"workflow:{profile.user_id}:{workflow.name}"
            self.ensure_node(
                node_id=workflow_node_id,
                label=workflow.name.replace("-", " "),
                kind="workflow",
                group="memory",
                metadata={
                    "pattern": workflow.pattern,
                    "source": workflow.source,
                },
            )
            self.ensure_edge(
                source=user_node_id,
                target=workflow_node_id,
                relation="uses_workflow",
                strength=0.85,
            )
        self._persist()

    def relevant_summary(self, query: str, limit: int = 8) -> list[str]:
        lowered = query.lower()
        scored_nodes: list[tuple[int, BrainGraphNode]] = []
        for node in self._graph.nodes:
            haystack = " ".join(
                [node.label, node.kind, node.group, *node.metadata.values()]
            ).lower()
            score = sum(1 for token in lowered.split() if token and token in haystack)
            if score > 0:
                scored_nodes.append((score, node))

        if not scored_nodes:
            return [
                f"{edge.source} {edge.relation} {edge.target}"
                for edge in self._graph.edges[:limit]
            ]

        selected_ids = {node.id for _, node in sorted(scored_nodes, key=lambda item: item[0], reverse=True)[:4]}
        summaries: list[str] = []
        for edge in self._graph.edges:
            if edge.source in selected_ids or edge.target in selected_ids:
                summaries.append(f"{edge.source} {edge.relation} {edge.target}")
            if len(summaries) >= limit:
                break
        return summaries

    def _persist(self) -> None:
        if self._backend:
            self._backend.save_graph(self._graph)


class InMemoryConversationStore:
    """Simple per-user, per-conversation chat memory."""

    def __init__(
        self,
        history_limit: int = 12,
        backend: PersistentStateBackend | None = None,
    ) -> None:
        self.history_limit = history_limit
        self._backend = backend
        self._conversations: dict[str, dict[str, list[ConversationMessage]]] = (
            backend.load_conversations() if backend else {}
        )

    def get_history(self, user_id: str, conversation_id: str) -> ConversationHistory:
        messages = self._conversations.get(user_id, {}).get(conversation_id, [])
        return ConversationHistory(
            conversation_id=conversation_id,
            messages=list(messages),
        )

    def append(
        self,
        user_id: str,
        conversation_id: str,
        role: str,
        content: str,
    ) -> ConversationHistory:
        user_conversations = self._conversations.setdefault(user_id, {})
        messages = user_conversations.setdefault(conversation_id, [])
        messages.append(ConversationMessage(role=role, content=content))
        if len(messages) > self.history_limit:
            user_conversations[conversation_id] = messages[-self.history_limit :]
        self._persist()
        return self.get_history(user_id, conversation_id)

    def clear(self, user_id: str, conversation_id: str) -> None:
        user_conversations = self._conversations.get(user_id, {})
        user_conversations.pop(conversation_id, None)
        self._persist()

    def _persist(self) -> None:
        if self._backend:
            self._backend.save_conversations(self._conversations)
