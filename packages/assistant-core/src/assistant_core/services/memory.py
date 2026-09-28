import json
import re
import tempfile
import threading
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from assistant_core.models import (
    AppAdapter,
    BrainGraph,
    BrainGraphEdge,
    BrainGraphNode,
    ContextPack,
    ConversationHistory,
    ConversationMessage,
    DeviceCapability,
    LANDevice,
    ImportHistoryEntry,
    PersistentRoutine,
    SessionMemory,
    TimelineEvent,
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
        self._save_lock = threading.Lock()
        self._state = {
            "profiles": {},
            "graph": _default_graph().model_dump(mode="json"),
            "conversations": {},
            "imports": [],
            "routines": {},
            "timeline": {},
            "context_packs": {},
            "session": {},
            "devices": {},
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
        self._state["routines"] = loaded.get("routines", {}) or {}
        self._state["timeline"] = loaded.get("timeline", {}) or {}
        self._state["context_packs"] = loaded.get("context_packs", {}) or {}
        self._state["session"] = loaded.get("session", {}) or {}
        self._state["devices"] = loaded.get("devices", {}) or {}

    def save(self) -> None:
        with self._save_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=self.path.name, suffix=".tmp", delete=False) as file:
                temp_path = Path(file.name)
                try:
                    json.dump(self._state, file, indent=2)
                except Exception:
                    temp_path.unlink(missing_ok=True)
                    raise
            try:
                temp_path.replace(self.path)
            finally:
                temp_path.unlink(missing_ok=True)

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

    def load_routines(self) -> dict[str, list[PersistentRoutine]]:
        routines: dict[str, list[PersistentRoutine]] = {}
        for user_id, entries in self._state.get("routines", {}).items():
            routines[user_id] = [PersistentRoutine.model_validate(entry) for entry in entries]
        return routines

    def save_routines(self, routines: dict[str, list[PersistentRoutine]]) -> None:
        self._state["routines"] = {
            user_id: [entry.model_dump(mode="json") for entry in entries]
            for user_id, entries in routines.items()
        }
        self.save()

    def load_timeline(self) -> dict[str, list[TimelineEvent]]:
        timeline: dict[str, list[TimelineEvent]] = {}
        for user_id, entries in self._state.get("timeline", {}).items():
            timeline[user_id] = [TimelineEvent.model_validate(entry) for entry in entries]
        return timeline

    def save_timeline(self, timeline: dict[str, list[TimelineEvent]]) -> None:
        self._state["timeline"] = {
            user_id: [entry.model_dump(mode="json") for entry in entries]
            for user_id, entries in timeline.items()
        }
        self.save()

    def load_context_packs(self) -> dict[str, list[ContextPack]]:
        packs: dict[str, list[ContextPack]] = {}
        for user_id, entries in self._state.get("context_packs", {}).items():
            packs[user_id] = [ContextPack.model_validate(entry) for entry in entries]
        return packs

    def save_context_packs(self, packs: dict[str, list[ContextPack]]) -> None:
        self._state["context_packs"] = {
            user_id: [entry.model_dump(mode="json") for entry in entries]
            for user_id, entries in packs.items()
        }
        self.save()

    def load_sessions(self) -> dict[str, SessionMemory]:
        return {
            user_id: SessionMemory.model_validate(entry)
            for user_id, entry in self._state.get("session", {}).items()
        }

    def save_sessions(self, sessions: dict[str, SessionMemory]) -> None:
        self._state["session"] = {
            user_id: entry.model_dump(mode="json")
            for user_id, entry in sessions.items()
        }
        self.save()

    def load_devices(self) -> dict[str, list[LANDevice]]:
        devices: dict[str, list[LANDevice]] = {}
        for user_id, entries in self._state.get("devices", {}).items():
            devices[user_id] = [LANDevice.model_validate(entry) for entry in entries]
        return devices

    def save_devices(self, devices: dict[str, list[LANDevice]]) -> None:
        self._state["devices"] = {
            user_id: [entry.model_dump(mode="json") for entry in entries]
            for user_id, entries in devices.items()
        }
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

    def relevant_summary(self, query: str, limit: int = 8, *, user_id: str | None = None) -> list[str]:
        """Retrieve readable saved facts, not scaffold edges or invented graph interpretations."""
        if limit <= 0:
            return []
        stop_words = {"a", "an", "the", "is", "are", "i", "me", "my", "you", "your", "it", "this", "that",
                      "to", "of", "and", "or", "in", "on", "do", "does", "can", "what", "why", "how", "about", "with"}
        tokens = set(re.findall(r"\w+", query.lower())) - stop_words
        personal = bool(re.search(r"\b(about me|remember|my preferences|know about me|my workflow)\b", query, re.I))
        user_node = f"user:{user_id}" if user_id else None
        owned = {edge.target for edge in self._graph.edges if edge.source == user_node}
        other_owned = {edge.target for edge in self._graph.edges
                       if edge.source.startswith("user:") and edge.source != user_node}
        eligible = {}
        for node in self._graph.nodes:
            if node.kind not in {"user", "preference", "workflow", "conversation", "fact", "goal", "document", "note"}:
                continue
            if user_id and (node.metadata.get("user_id", user_id) != user_id
                            or (node.kind == "user" and node.id != user_node)
                            or (node.kind in {"preference", "workflow"}
                                and node.id.startswith(f"{node.kind}:")
                                and not node.id.startswith(f"{node.kind}:{user_id}:"))
                            or (node.id in other_owned and node.id not in owned)):
                continue
            eligible[node.id] = node
        ranked = []
        for node in eligible.values():
            words = set(re.findall(r"\w+", " ".join([node.label, *node.metadata.values()]).lower()))
            score = len(tokens & words) * 10
            if node.kind in {"preference", "workflow", "user"}:
                score += 4 if personal else 1
            if score:
                ranked.append((score, node))
        selected = [node for _, node in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:min(limit, 5)]]
        # Add one-hop factual neighbors; do not traverse other users or the infrastructure scaffold.
        selected_ids = {node.id for node in selected}
        roots = selected_ids.copy()
        for edge in self._graph.edges:
            if len(selected) >= min(limit, 8):
                break
            neighbor = edge.target if edge.source in roots else edge.source if edge.target in roots else None
            if neighbor in eligible and neighbor not in selected_ids:
                selected.append(eligible[neighbor])
                selected_ids.add(neighbor)
        summaries = []
        for node in selected:
            facts = {key: value[:240] for key, value in node.metadata.items()
                     if key in {"value", "pattern", "display_name", "user_excerpt", "summary", "description", "source"}}
            summaries.append(f"{node.label[:100]} ({node.kind}): {json.dumps(facts, ensure_ascii=True)}"[:400])
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
        *,
        local_only: bool = False,
    ) -> ConversationHistory:
        user_conversations = self._conversations.setdefault(user_id, {})
        messages = user_conversations.setdefault(conversation_id, [])
        messages.append(ConversationMessage(role=role, content=content, local_only=local_only))
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


class InMemoryRoutineStore:
    def __init__(self, backend: PersistentStateBackend | None = None) -> None:
        self._backend = backend
        self._routines: dict[str, list[PersistentRoutine]] = backend.load_routines() if backend else {}

    def list_routines(self, user_id: str) -> list[PersistentRoutine]:
        return list(self._routines.get(user_id, []))

    def upsert(
        self,
        user_id: str,
        *,
        name: str,
        trigger: str,
        instructions: str,
        enabled: bool = True,
        source: str = "manual",
    ) -> PersistentRoutine:
        existing = next((item for item in self._routines.setdefault(user_id, []) if item.name.lower() == name.lower()), None)
        now = datetime.now(timezone.utc)
        if existing:
            existing.trigger = trigger
            existing.instructions = instructions
            existing.enabled = enabled
            existing.source = source
            existing.updated_at = now
            self._persist()
            return existing
        routine = PersistentRoutine(
            routine_id=f"routine-{uuid4().hex[:12]}",
            name=name,
            trigger=trigger,
            instructions=instructions,
            enabled=enabled,
            source=source,
            updated_at=now,
        )
        self._routines[user_id].append(routine)
        self._persist()
        return routine

    def _persist(self) -> None:
        if self._backend:
            self._backend.save_routines(self._routines)


class InMemoryTimelineStore:
    def __init__(self, backend: PersistentStateBackend | None = None, limit: int = 200) -> None:
        self._backend = backend
        self.limit = limit
        self._timeline: dict[str, list[TimelineEvent]] = backend.load_timeline() if backend else {}

    def list_events(self, user_id: str, limit: int = 40) -> list[TimelineEvent]:
        entries = self._timeline.get(user_id, [])
        return list(entries[-limit:])[::-1]

    def record(
        self,
        user_id: str,
        *,
        event_type: str,
        title: str,
        detail: str | None = None,
        app_name: str | None = None,
        file_paths: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> TimelineEvent:
        entry = TimelineEvent(
            event_id=f"timeline-{uuid4().hex[:12]}",
            event_type=event_type,
            title=title,
            detail=detail,
            app_name=app_name,
            file_paths=file_paths or [],
            metadata=metadata or {},
            recorded_at=datetime.now(timezone.utc),
        )
        items = self._timeline.setdefault(user_id, [])
        items.append(entry)
        if len(items) > self.limit:
            self._timeline[user_id] = items[-self.limit :]
        self._persist()
        return entry

    def _persist(self) -> None:
        if self._backend:
            self._backend.save_timeline(self._timeline)


class InMemoryContextPackStore:
    def __init__(self, backend: PersistentStateBackend | None = None) -> None:
        self._backend = backend
        self._packs: dict[str, list[ContextPack]] = backend.load_context_packs() if backend else {}

    def list_packs(self, user_id: str) -> list[ContextPack]:
        return list(self._packs.get(user_id, []))

    def add_pack(
        self,
        user_id: str,
        *,
        title: str,
        source_type: str,
        root_path: str,
        summary: str,
        file_paths: list[str],
    ) -> ContextPack:
        pack = ContextPack(
            pack_id=f"pack-{uuid4().hex[:12]}",
            title=title,
            source_type=source_type,
            root_path=root_path,
            summary=summary,
            file_count=len(file_paths),
            file_paths=file_paths,
            created_at=datetime.now(timezone.utc),
        )
        self._packs.setdefault(user_id, []).append(pack)
        self._persist()
        return pack

    def _persist(self) -> None:
        if self._backend:
            self._backend.save_context_packs(self._packs)


class InMemorySessionStore:
    def __init__(self, backend: PersistentStateBackend | None = None) -> None:
        self._backend = backend
        self._sessions: dict[str, SessionMemory] = backend.load_sessions() if backend else {}

    def get_session(self, user_id: str) -> SessionMemory:
        return self._sessions.setdefault(user_id, SessionMemory(user_id=user_id))

    def update(
        self,
        user_id: str,
        *,
        active_goal: str | None = None,
        active_app: str | None = None,
        active_files: list[str] | None = None,
        active_tasks: list[str] | None = None,
        last_context_pack_id: str | None = None,
    ) -> SessionMemory:
        session = self.get_session(user_id)
        if active_goal is not None:
            session.active_goal = active_goal
        if active_app is not None:
            session.active_app = active_app
        if active_files is not None:
            session.active_files = active_files
        if active_tasks is not None:
            session.active_tasks = active_tasks
        if last_context_pack_id is not None:
            session.last_context_pack_id = last_context_pack_id
        session.updated_at = datetime.now(timezone.utc)
        self._persist()
        return session

    def clear(self, user_id: str) -> SessionMemory:
        session = SessionMemory(user_id=user_id, updated_at=datetime.now(timezone.utc))
        self._sessions[user_id] = session
        self._persist()
        return session

    def _persist(self) -> None:
        if self._backend:
            self._backend.save_sessions(self._sessions)


class InMemoryDeviceStore:
    def __init__(self, backend: PersistentStateBackend | None = None) -> None:
        self._backend = backend
        self._devices: dict[str, list[LANDevice]] = backend.load_devices() if backend else {}

    def list_devices(self, user_id: str) -> list[LANDevice]:
        devices = list(self._devices.get(user_id, []))
        return sorted(
            devices,
            key=lambda device: (
                0 if device.trust_state == "trusted" else 1,
                device.name.lower(),
            ),
        )

    def upsert(
        self,
        user_id: str,
        *,
        device_id: str | None,
        name: str,
        device_type: str,
        hostname: str | None = None,
        ip_address: str | None = None,
        status: str = "available",
        trust_state: str = "trusted",
        agent_version: str | None = None,
        protocols: list[str] | None = None,
        capabilities: list | None = None,
        notes: str | None = None,
    ) -> LANDevice:
        entries = self._devices.setdefault(user_id, [])
        normalized_device_id = device_id or f"device-{uuid4().hex[:12]}"
        existing = next(
            (
                item for item in entries
                if item.device_id == normalized_device_id
                or (hostname and item.hostname and item.hostname.lower() == hostname.lower())
                or (ip_address and item.ip_address == ip_address)
            ),
            None,
        )
        now = datetime.now(timezone.utc)
        normalized_protocols = list(dict.fromkeys(protocols or []))
        normalized_capabilities = [
            capability if isinstance(capability, DeviceCapability) else DeviceCapability.model_validate(capability)
            for capability in (capabilities or [])
        ]
        if existing:
            if device_id:
                existing.device_id = device_id
            existing.name = name
            existing.device_type = device_type
            existing.hostname = hostname
            existing.ip_address = ip_address
            existing.status = status
            existing.trust_state = trust_state
            existing.agent_version = agent_version
            existing.protocols = normalized_protocols
            existing.capabilities = normalized_capabilities
            existing.notes = notes
            existing.last_seen_at = now
            existing.updated_at = now
            self._persist()
            return existing

        device = LANDevice(
            device_id=normalized_device_id,
            name=name,
            device_type=device_type,
            hostname=hostname,
            ip_address=ip_address,
            status=status,
            trust_state=trust_state,
            agent_version=agent_version,
            protocols=normalized_protocols,
            capabilities=normalized_capabilities,
            notes=notes,
            last_seen_at=now,
            updated_at=now,
        )
        entries.append(device)
        self._persist()
        return device

    def record_seen(
        self,
        user_id: str,
        device_id: str,
        *,
        status: str | None = None,
        ip_address: str | None = None,
    ) -> LANDevice | None:
        entries = self._devices.get(user_id, [])
        existing = next((item for item in entries if item.device_id == device_id), None)
        if existing is None:
            return None
        now = datetime.now(timezone.utc)
        existing.last_seen_at = now
        existing.updated_at = now
        if status is not None:
            existing.status = status
        if ip_address is not None:
            existing.ip_address = ip_address
        self._persist()
        return existing

    def _persist(self) -> None:
        if self._backend:
            self._backend.save_devices(self._devices)
