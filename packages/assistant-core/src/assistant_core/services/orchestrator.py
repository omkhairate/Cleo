import json
import re
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator

from assistant_core.connectors.registry import ConnectorRegistry
from assistant_core.config import get_settings
from assistant_core.models import (
    AppAdapter,
    BrainGraph,
    ChatReply,
    ChatRequest,
    ContextPack,
    ContextPackBuildRequest,
    DeviceUpsertRequest,
    ChatGPTImportReply,
    ChatGPTImportRequest,
    CommandReply,
    CommandRequest,
    ConnectorSummary,
    ConversationHistory,
    ImportHistoryEntry,
    InteractionReply,
    InteractionRequest,
    LANDevice,
    PersistentRoutine,
    RequestClassification,
    RequestRouteCandidate,
    RoutineUpsertRequest,
    SessionMemory,
    TimelineEvent,
    UserProfile,
    UserProfileUpdate,
    VisualContextPayload,
    WorkspaceMemorySnapshot,
)
from assistant_core.services.agent_workflow import AgentWorkflowService
from assistant_core.services.argus_memory import ArgusGraphSync, ArgusMemoryClient
from assistant_core.services.context_builder import ContextBuilder
from assistant_core.services.llm import LLMError, RoutingLLMService
from assistant_core.services.memory_extractor import MemoryExtractor
from assistant_core.services.memory import (
    InMemoryBrainGraphStore,
    InMemoryConversationStore,
    InMemoryContextPackStore,
    InMemoryDeviceStore,
    InMemoryProfileStore,
    InMemoryRoutineStore,
    InMemorySessionStore,
    InMemoryTimelineStore,
    PersistentStateBackend,
)
from assistant_core.services.tool_registry import CommandToolRegistry
from assistant_core.services.proactivity import ProactivityService
from assistant_core.services.feature_facts import feature_answer
from assistant_core.services.file_evidence import FileEvidenceService


class AssistantOrchestrator:
    """Coordinates memory, routing, and connector discovery."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.state_backend = PersistentStateBackend(self.settings.state_file_path)
        self.registry = ConnectorRegistry()
        self.memory = InMemoryProfileStore(self.state_backend)
        self.brain_graph = InMemoryBrainGraphStore(self.state_backend)
        self.conversations = InMemoryConversationStore(
            history_limit=self.settings.conversation_history_limit,
            backend=self.state_backend,
        )
        self.routines = InMemoryRoutineStore(self.state_backend)
        self.timeline = InMemoryTimelineStore(self.state_backend)
        self.context_packs = InMemoryContextPackStore(self.state_backend)
        self.sessions = InMemorySessionStore(self.state_backend)
        self.devices = InMemoryDeviceStore(self.state_backend)
        self.import_history = self.state_backend.load_import_history()
        self.proactivity = ProactivityService(self.state_backend.path.with_name("proactivity.sqlite3"))
        self.file_evidence = FileEvidenceService(self.state_backend.path.with_name("file-evidence.sqlite3"))
        self.context_builder = ContextBuilder(self.proactivity, self.file_evidence)
        self.memory_extractor = MemoryExtractor(self.memory, self.brain_graph)
        self.llm = RoutingLLMService(self.settings)
        self.argus = ArgusMemoryClient(self.settings)
        self.argus_graph_sync = ArgusGraphSync(self.argus, self.brain_graph)
        self.command_tools = CommandToolRegistry(
            self.settings,
            self.memory,
            self.brain_graph,
            self.conversations,
        )
        self.agent_workflow = AgentWorkflowService(
            self.llm,
            self.command_tools,
            self.memory_extractor,
        )
        self._seed_defaults()

    def reply(
        self,
        request: ChatRequest,
        model_message: str | None = None,
        visual_context: VisualContextPayload | None = None,
        classification: RequestClassification | None = None,
    ) -> ChatReply:
        user_id = request.user_id or "local-user"
        conversation_id = request.conversation_id or "default"
        factual = self._feature_reply(request.message, user_id, conversation_id, allow_files=visual_context is None)
        if factual is not None:
            return ChatReply(reply=factual.response, conversation_id=conversation_id, provider=factual.provider, model=factual.model)
        profile = self.memory_extractor.ingest_user_message(user_id, request.message)
        profile = self.memory.get_profile(user_id)
        app_name = "Cleo"
        graph = self.brain_graph.get_graph()
        history = self.conversations.get_history(user_id, conversation_id)
        argus_context = self._argus_context_for_message(model_message or request.message)
        message_for_model = model_message or request.message
        if argus_context:
            message_for_model = f"{message_for_model}\n\nArgus episodic memory context:\n{argus_context}"
        graph_summary = self._graph_memory_for_request(request.message, history, user_id)
        context = self.context_builder.build(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=graph_summary,
            query=request.message if visual_context is None else "",
        )

        next_steps = []

        try:
            llm_reply = self.llm.chat(
                ChatRequest(
                    message=message_for_model,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                ),
                context,
                history,
                visual_context,
                stack_hint=classification.stack if classification and classification.mode == "chat" else None,
            )
            reply = self._with_file_sources(llm_reply.content, context)
            self.conversations.append(user_id, conversation_id, "user", request.message, local_only=bool(context.file_evidence))
            self.conversations.append(user_id, conversation_id, "assistant", reply, local_only=bool(context.file_evidence))
            self._record_timeline_event(
                user_id=user_id,
                event_type="chat",
                title=request.message,
                detail=reply[:240],
                visual_context=visual_context,
            )
            self._update_session_memory(
                user_id=user_id,
                goal=request.message,
                visual_context=visual_context,
                active_tasks=["chat"],
            )
        except LLMError as exc:
            reply = (
                f"{app_name} is configured to use the local model '{self.settings.local_model_id}', "
                f"but the runtime is not ready yet. {exc} "
                "Once the configured local runtime is ready, Cleo will answer through the model instead of scaffolded text."
            )
            next_steps = [
                f"Prepare the local model '{self.settings.local_model_id}'.",
                "Install any missing local runtime dependencies for the configured provider.",
                "Retry the same chat request once the local runtime is healthy.",
            ]

        used_connectors = ["argus"] if argus_context else []
        lowered = request.message.lower()
        for connector in self.registry.list():
            if connector.key in lowered or connector.name.lower() in lowered:
                used_connectors.append(connector.key)

        return ChatReply(
            reply=reply,
            conversation_id=conversation_id,
            provider=getattr(llm_reply, "provider", None) if "llm_reply" in locals() else None,
            model=getattr(llm_reply, "model", None) if "llm_reply" in locals() else None,
            used_connectors=used_connectors,
            next_steps=next_steps,
        )

    def list_connectors(self) -> list[ConnectorSummary]:
        return [
            ConnectorSummary(
                key=connector.key,
                name=connector.name,
                description=connector.description,
                auth_required=connector.auth_required,
            )
            for connector in self.registry.list()
        ]

    def get_brain_graph(self) -> BrainGraph:
        return self.brain_graph.get_graph()

    def query_argus_memory(self, query: str, limit: int = 8) -> dict:
        return self.argus.query_memory(query, limit=limit)

    def sync_argus_graph(self) -> BrainGraph:
        graph, _ = self.argus_graph_sync.sync()
        return graph

    def ingest_argus_context(self, text: str, source: str = "cleo") -> dict:
        return self.argus.ingest_context(text, source=source)

    def get_model_status(self) -> dict[str, str]:
        return self.llm.check_health()

    def warmup_runtime(self, *, text_only: bool = True) -> dict[str, str]:
        return self.llm.warmup(text_only=text_only)

    def list_app_adapters(self) -> list[AppAdapter]:
        return self._app_adapters()

    def list_devices(self, user_id: str = "local-user") -> list[LANDevice]:
        return self.devices.list_devices(user_id)

    def upsert_device(self, request: DeviceUpsertRequest) -> LANDevice:
        user_id = request.user_id or "local-user"
        device = self.devices.upsert(
            user_id,
            device_id=request.device_id,
            name=request.name,
            device_type=request.device_type,
            hostname=request.hostname,
            ip_address=request.ip_address,
            status=request.status,
            trust_state=request.trust_state,
            agent_version=request.agent_version,
            protocols=request.protocols,
            capabilities=request.capabilities,
            notes=request.notes,
        )
        self._sync_device_graph(user_id, device)
        self.timeline.record(
            user_id,
            event_type="device",
            title=f"Updated device: {device.name}",
            detail=f"{device.device_type} • {device.status} • {device.trust_state}",
            metadata={
                "device_id": device.device_id,
                "hostname": device.hostname or "",
                "ip_address": device.ip_address or "",
            },
        )
        return device

    def list_routines(self, user_id: str = "local-user") -> list[PersistentRoutine]:
        return self.routines.list_routines(user_id)

    def upsert_routine(self, request: RoutineUpsertRequest) -> PersistentRoutine:
        user_id = request.user_id or "local-user"
        routine = self.routines.upsert(
            user_id,
            name=request.name,
            trigger=request.trigger,
            instructions=request.instructions,
            enabled=request.enabled,
            source=request.source,
        )
        self.timeline.record(
            user_id,
            event_type="routine",
            title=f"Updated routine: {routine.name}",
            detail=routine.trigger,
            metadata={"routine_id": routine.routine_id, "enabled": str(routine.enabled).lower()},
        )
        return routine

    def get_timeline(self, user_id: str = "local-user", limit: int = 40) -> list[TimelineEvent]:
        return self.timeline.list_events(user_id, limit=limit)

    def get_session_memory(self, user_id: str = "local-user") -> SessionMemory:
        return self.sessions.get_session(user_id)

    def clear_session_memory(self, user_id: str = "local-user") -> SessionMemory:
        return self.sessions.clear(user_id)

    def list_context_packs(self, user_id: str = "local-user") -> list[ContextPack]:
        return self.context_packs.list_packs(user_id)

    def build_context_pack(self, request: ContextPackBuildRequest) -> ContextPack:
        user_id = request.user_id or "local-user"
        root = Path(request.file_path).expanduser().resolve()
        pack = self._build_context_pack(user_id, root, request.title)
        self._update_session_memory(
            user_id=user_id,
            goal=None,
            visual_context=None,
            active_files=[str(root)],
            last_context_pack_id=pack.pack_id,
        )
        self.timeline.record(
            user_id,
            event_type="context-pack",
            title=f"Built context pack: {pack.title}",
            detail=pack.summary,
            file_paths=[pack.root_path, *pack.file_paths[:6]],
            metadata={"pack_id": pack.pack_id, "source_type": pack.source_type},
        )
        return pack

    def get_workspace_memory_snapshot(self, user_id: str = "local-user") -> WorkspaceMemorySnapshot:
        return WorkspaceMemorySnapshot(
            profile=self.get_profile(user_id),
            graph=self.get_brain_graph(),
            imports=self.get_import_history(),
            adapters=self.list_app_adapters(),
            devices=self.list_devices(user_id),
            routines=self.list_routines(user_id),
            timeline=self.get_timeline(user_id),
            context_packs=self.list_context_packs(user_id),
            session=self.get_session_memory(user_id),
        )

    def interact(self, request: InteractionRequest) -> InteractionReply:
        factual = self._feature_reply(request.message, request.user_id or "local-user", request.conversation_id or "auto-chat", allow_files=request.visual_context is None)
        if factual is not None:
            return factual
        request = self._resolve_conversation_followup(request)
        classification, route_candidates = self._classify_interaction(request)
        mode = classification.mode
        enriched_message = self._merge_visual_context(request.message, request.visual_context)
        if mode == "command":
            result = self.run_command(
                CommandRequest(
                    command=request.message,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id or "auto-command",
                ),
                model_message=enriched_message,
            )
            if request.response_mode == "reviewed":
                result.final_response = self._review_response(
                    original_message=request.message,
                    draft=result.final_response,
                    context=result.summary or "Command workflow completed.",
                    user_id=request.user_id,
                    conversation_id=result.conversation_id,
                    include_files=request.visual_context is None and bool(re.search(r"\.(?:pdf|docx|txt|md)\b|my files|my documents", request.message, re.I)),
                )
            reply = InteractionReply(
                mode="command",
                conversation_id=result.conversation_id,
                response=result.final_response,
                provider=result.provider,
                model=result.model,
                summary=result.summary,
                tasks=result.tasks,
                executions=result.executions,
                classification=classification,
                route_candidates=route_candidates,
            )
            if result.executions and all(execution.status == "blocked" for execution in result.executions):
                fallback = self._fallback_interaction_reply(
                    request=request,
                    route_candidates=route_candidates,
                    current_classification=classification,
                    enriched_message=enriched_message,
                )
                if fallback is not None:
                    return fallback
            return reply

        result = self.reply(
            ChatRequest(
                message=request.message,
                user_id=request.user_id,
                conversation_id=request.conversation_id or "auto-chat",
            ),
            model_message=enriched_message,
            visual_context=request.visual_context,
            classification=classification,
        )
        response_text = result.reply
        if request.response_mode == "reviewed" and result.provider:
            response_text = self._review_response(
                original_message=request.message,
                draft=result.reply,
                context=enriched_message,
                user_id=request.user_id,
                conversation_id=result.conversation_id,
                include_files=request.visual_context is None,
            )
        return InteractionReply(
            mode="chat",
            conversation_id=result.conversation_id,
            response=response_text,
            provider=result.provider,
            model=result.model,
            classification=classification,
            route_candidates=route_candidates,
        )

    def stream_interaction_events(self, request: InteractionRequest) -> Iterator[dict]:
        factual = self._feature_reply(request.message, request.user_id or "local-user", request.conversation_id or "auto-chat", allow_files=request.visual_context is None)
        if factual is not None:
            yield {"type": "meta", "mode": "chat", "classification": factual.classification.model_dump(mode="json"), "route_candidates": []}
            yield {"type": "delta", "mode": "chat", "conversation_id": factual.conversation_id, "response": factual.response}
            yield {"type": "final", **factual.model_dump(mode="json")}
            return
        request = self._resolve_conversation_followup(request)
        classification, route_candidates = self._classify_interaction(request)
        mode = classification.mode
        enriched_message = self._merge_visual_context(request.message, request.visual_context)
        yield {
            "type": "meta",
            "mode": mode,
            "classification": classification.model_dump(mode="json"),
            "route_candidates": [candidate.model_dump(mode="json") for candidate in route_candidates],
        }

        if mode != "command":
            user_id = request.user_id or "local-user"
            conversation_id = request.conversation_id or "auto-chat"
            profile = self.memory_extractor.ingest_user_message(user_id, request.message)
            graph = self.brain_graph.get_graph()
            history = self.conversations.get_history(user_id, conversation_id)
            graph_summary = self._graph_memory_for_request(request.message, history, user_id)
            context = self.context_builder.build(
                user_id=user_id,
                conversation_id=conversation_id,
                profile=profile,
                history=history,
                graph=graph,
                graph_summary=graph_summary,
                query=request.message if request.visual_context is None else "",
            )

            chunks: list[str] = []
            provider: str | None = None
            model: str | None = None
            try:
                provider, model = self.llm.stream_model_identity(
                    ChatRequest(message=enriched_message), request.visual_context
                )
                for chunk in self.llm.stream_chat(
                    ChatRequest(
                        message=enriched_message,
                        user_id=request.user_id,
                        conversation_id=conversation_id,
                    ),
                    context,
                    history,
                    request.visual_context,
                    stack_hint=classification.stack if classification.mode == "chat" else None,
                ):
                    chunks.append(chunk)
                    yield {
                        "type": "delta",
                        "mode": "chat",
                        "conversation_id": conversation_id,
                        "response": chunk,
                    }
            except LLMError as exc:
                yield {
                    "type": "final",
                    "mode": "chat",
                    "conversation_id": conversation_id,
                    "response": (
                        f"Cleo could not stream a response from the local model '{self.settings.local_model_id}'. "
                        f"{exc}"
                    ),
                    "provider": None,
                    "model": None,
                    "summary": None,
                    "tasks": [],
                }
                return

            response_text = self._with_file_sources("".join(chunks).strip(), context)
            if response_text:
                self.conversations.append(user_id, conversation_id, "user", request.message, local_only=bool(context.file_evidence))
                self.conversations.append(user_id, conversation_id, "assistant", response_text, local_only=bool(context.file_evidence))
                self._record_timeline_event(
                    user_id=user_id,
                    event_type="chat",
                    title=request.message,
                    detail=response_text[:240],
                    visual_context=request.visual_context,
                )
                self._update_session_memory(
                    user_id=user_id,
                    goal=request.message,
                    visual_context=request.visual_context,
                    active_tasks=["chat"],
                )

            if request.response_mode == "reviewed" and provider:
                response_text = self._review_response(
                    original_message=request.message,
                    draft=response_text,
                    context=enriched_message,
                    user_id=request.user_id,
                    conversation_id=conversation_id,
                    include_files=request.visual_context is None,
                )
            yield {
                "type": "final",
                "mode": "chat",
                "conversation_id": conversation_id,
                "response": response_text,
                "provider": provider,
                "model": model,
                "summary": None,
                "tasks": [],
            }
            return

        user_id = request.user_id or "local-user"
        conversation_id = request.conversation_id or "auto-command"
        command_for_model = enriched_message
        profile = self.memory_extractor.ingest_user_message(user_id, request.message)
        graph = self.brain_graph.get_graph()
        history = self.conversations.get_history(user_id, conversation_id)
        graph_summary = self._graph_memory_for_request(request.message, history, user_id)
        context = self.context_builder.build(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=graph_summary,
            query=request.message if re.search(r"\.(?:pdf|docx|txt|md)\b|my files|my documents", request.message, re.I) else "",
        )

        command_request = CommandRequest(
            command=request.message,
            user_id=request.user_id,
            conversation_id=conversation_id,
        )
        # Captured screen text is context, never an executable instruction.
        tasks = self.agent_workflow.plan(request.message)
        yield {
            "type": "planned",
            "mode": "command",
            "conversation_id": conversation_id,
            "tasks": [task.model_dump(mode="json") for task in tasks],
        }

        executions = []
        for task in tasks:
            execution = self.agent_workflow.execute_one(
                task,
                user_id=user_id,
                conversation_id=conversation_id,
                context=context,
                history=history,
            )
            task.status = execution.status
            task.output = execution.output
            executions.append(execution)
            yield {
                "type": "task",
                "mode": "command",
                "conversation_id": conversation_id,
                "task": task.model_dump(mode="json"),
                "execution": execution.model_dump(mode="json"),
            }

        if (
            request.response_mode == "fast"
            and self.agent_workflow.can_compose_direct_final(tasks=tasks, executions=executions)
        ):
            llm_reply = self.agent_workflow.compose_direct_final(tasks=tasks, executions=executions)
        else:
            llm_reply = self.agent_workflow.compose_final(
                request=command_request,
                context=context,
                history=history,
                tasks=tasks,
                executions=executions,
            )
        final_response = self._with_file_sources(llm_reply.content, context)
        summary = (
            f"Planned {len(tasks)} tasks across "
            f"{len({task.specialist for task in tasks})} specialist roles."
        )
        if request.response_mode == "reviewed":
            final_response = self._review_response(
                original_message=request.message,
                draft=final_response,
                context=summary,
                user_id=request.user_id,
                conversation_id=conversation_id,
                include_files=request.visual_context is None and bool(context.file_evidence),
            )

        self.conversations.append(user_id, conversation_id, "user", request.message, local_only=bool(context.file_evidence))
        self.conversations.append(user_id, conversation_id, "assistant", final_response, local_only=bool(context.file_evidence))

        yield {
            "type": "final",
            "mode": "command",
            "conversation_id": conversation_id,
            "response": final_response,
            "provider": llm_reply.provider,
            "model": llm_reply.model,
            "summary": summary,
            "tasks": [task.model_dump(mode="json") for task in tasks],
        }

    def run_command(
        self,
        request: CommandRequest,
        model_message: str | None = None,
    ) -> CommandReply:
        user_id = request.user_id or "local-user"
        conversation_id = request.conversation_id or "command"
        command_for_model = model_message or request.command
        profile = self.memory_extractor.ingest_user_message(user_id, request.command)
        graph = self.brain_graph.get_graph()
        history = self.conversations.get_history(user_id, conversation_id)
        graph_summary = self._graph_memory_for_request(request.command, history, user_id)
        context = self.context_builder.build(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=graph_summary,
            query=request.command if re.search(r"\.(?:pdf|docx|txt|md)\b|my files|my documents", request.command, re.I) else "",
        )

        result = self.agent_workflow.execute(
            CommandRequest(
                command=request.command,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
            ),
            context=context,
            history=history,
        )

        self.conversations.append(user_id, conversation_id, "user", request.command, local_only=bool(context.file_evidence))
        self.conversations.append(user_id, conversation_id, "assistant", result.final_response, local_only=bool(context.file_evidence))
        self._record_timeline_event(
            user_id=user_id,
            event_type="command",
            title=request.command,
            detail=result.summary,
            command_reply=result,
        )
        self._update_session_memory_for_command(
            user_id=user_id,
            command=request.command,
            result=result,
        )
        return result

    def import_chatgpt_export(self, request: ChatGPTImportRequest) -> ChatGPTImportReply:
        user_id = request.user_id or "local-user"
        path = Path(request.file_path).expanduser().resolve()
        data = json.loads(path.read_text())
        conversations = data if isinstance(data, list) else data.get("conversations") if isinstance(data, dict) else None
        if not isinstance(conversations, list):
            raise ValueError("Choose a ChatGPT conversations export containing a list of conversations.")

        imported_conversations = 0
        imported_messages = 0
        imported_user_messages = 0

        for index, item in enumerate(conversations):
            if not isinstance(item, dict):
                continue
            messages = self._extract_chatgpt_messages(item)
            if not messages:
                continue
            imported_conversations += 1
            title = item.get("title") or f"import-{index + 1}"
            conversation_id = f"import-{index + 1}-{self._slugify(title)}"
            for role, content in messages:
                if not content.strip():
                    continue
                self.conversations.append(user_id, conversation_id, role, content)
                imported_messages += 1
                if role == "user":
                    imported_user_messages += 1
                    self.memory_extractor.ingest_user_message(user_id, content)
            self.brain_graph.ensure_node(
                node_id=f"conversation:{conversation_id}",
                label=title,
                kind="conversation",
                group="history",
                metadata={
                    "source": "chatgpt-export", "user_id": user_id,
                    "user_excerpt": " | ".join(content[:240] for role, content in messages if role == "user")[:720],
                },
            )
            self.brain_graph.ensure_edge(
                source=f"user:{user_id}",
                target=f"conversation:{conversation_id}",
                relation="discussed_in",
                strength=0.6,
            )

        profile = self.memory.get_profile(user_id)
        self.brain_graph.sync_profile(profile)
        reply = ChatGPTImportReply(
            file_path=str(path),
            imported_conversations=imported_conversations,
            imported_messages=imported_messages,
            imported_user_messages=imported_user_messages,
            profile_preferences=len(profile.preferences),
            profile_workflows=len(profile.workflows),
        )
        self.import_history.insert(
            0,
            ImportHistoryEntry(
                file_path=str(path),
                imported_at=datetime.now(timezone.utc),
                imported_conversations=imported_conversations,
                imported_messages=imported_messages,
                imported_user_messages=imported_user_messages,
            ),
        )
        self.import_history = self.import_history[:50]
        self.state_backend.save_import_history(self.import_history)
        self.timeline.record(
            user_id,
            event_type="import",
            title=f"Imported ChatGPT export: {path.name}",
            detail=f"{imported_conversations} conversations, {imported_user_messages} user messages",
            file_paths=[str(path)],
            metadata={"source": "chatgpt"},
        )
        return reply

    def get_import_history(self) -> list[ImportHistoryEntry]:
        return list(self.import_history)

    def _merge_visual_context(
        self,
        message: str,
        visual_context: VisualContextPayload | None,
    ) -> str:
        if visual_context is None:
            return message

        if visual_context.selected_text:
            return (
                "The user explicitly selected the following text. Answer about this exact selection.\n"
                f"Selected text:\n{visual_context.selected_text}\n\nRequest: {message}"
            )

        details: list[str] = []
        if visual_context.region_description:
            details.append(f"Region: {visual_context.region_description}")
        if visual_context.summary:
            details.append(f"Summary: {visual_context.summary}")
        if visual_context.selected_text:
            details.append(f"Selected text:\n{visual_context.selected_text}")
        if visual_context.ocr_text:
            details.append(f"OCR:\n{visual_context.ocr_text}")
        if visual_context.image_path:
            details.append(f"Captured image path: {visual_context.image_path}")

        if not details:
            return message

        visual_block = "\n".join(details)
        selection_instruction = ""
        if visual_context.selected_text:
            selection_instruction = (
                "The user explicitly selected text before asking the question. "
                "Treat that selected text as the primary target and answer about that exact selection unless the user clearly asks for broader surrounding context.\n"
            )
        pointer_instruction = ""
        if visual_context.source == "window-context":
            pointer_instruction = (
                "No explicit text selection was detected. Use the attached full window or app capture as the broader visual context. "
                "Prioritize the area near where the user invoked Cleo, but keep the rest of the visible application in mind.\n"
            )
        elif visual_context.source == "pointer-focus":
            pointer_instruction = (
                "No explicit text selection was detected. Use the attached pointer-centered image and nearby OCR as focused visual context, "
                "prioritizing the center of the captured region over surrounding page furniture.\n"
            )
        return (
            "Visual context captured near the user's pointer is included below.\n"
            f"{selection_instruction}"
            f"{pointer_instruction}"
            f"{visual_block}\n\n"
            f"User request:\n{message}"
        )

    def stream_reply(self, request: ChatRequest) -> Iterator[str]:
        user_id = request.user_id or "local-user"
        conversation_id = request.conversation_id or "default"
        factual = self._feature_reply(request.message, user_id, conversation_id)
        if factual is not None:
            yield factual.response
            return
        profile = self.memory_extractor.ingest_user_message(user_id, request.message)
        graph = self.brain_graph.get_graph()
        history = self.conversations.get_history(user_id, conversation_id)
        context = self.context_builder.build(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=self._graph_memory_for_request(request.message, history, user_id),
            query=request.message,
        )

        chunks: list[str] = []

        try:
            for chunk in self.llm.stream_chat(request, context, history):
                chunks.append(chunk)
                yield chunk
        except LLMError as exc:
            yield (
                f"Cleo could not stream a response from the local model '{self.settings.local_model_id}'. "
                f"{exc}"
            )
            return

        reply = self._with_file_sources("".join(chunks).strip(), context)
        if context.file_evidence:
            yield reply[len("".join(chunks).strip()):]
        if reply:
            self.conversations.append(user_id, conversation_id, "user", request.message, local_only=bool(context.file_evidence))
            self.conversations.append(user_id, conversation_id, "assistant", reply, local_only=bool(context.file_evidence))

    def get_conversation_history(
        self,
        user_id: str,
        conversation_id: str,
    ) -> ConversationHistory:
        return self.conversations.get_history(user_id, conversation_id)

    def clear_conversation_history(self, user_id: str, conversation_id: str) -> None:
        self.conversations.clear(user_id, conversation_id)

    def get_profile(self, user_id: str) -> UserProfile:
        return self.memory.get_profile(user_id)

    def update_profile(self, user_id: str, update: UserProfileUpdate) -> UserProfile:
        if update.display_name:
            self.memory.set_display_name(user_id, update.display_name)
        for key, value in update.preferences.items():
            self.memory.set_preference(user_id, key, value)
        profile = self.memory.get_profile(user_id)
        self.brain_graph.sync_profile(profile)
        return profile

    def _argus_context_for_message(self, message: str) -> str:
        if not self._should_query_argus(message):
            return ""
        try:
            result = self.argus.query_memory(message, limit=4)
        except Exception:
            return ""
        memories = result.get("memories", [])
        if not memories:
            return ""
        lines = [result.get("answer", "")]
        for memory in memories[:4]:
            lines.append(
                f"- {memory.get('captured_at')} [{memory.get('source')}] "
                f"{memory.get('caption')} (score={memory.get('score')})"
            )
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _should_query_argus(message: str) -> bool:
        lowered = message.lower()
        markers = [
            "where did i",
            "what did i",
            "when did i",
            "did i leave",
            "where is my",
            "earlier",
            "today",
            "yesterday",
            "this morning",
            "after lunch",
            "before lunch",
            "saw",
            "wearing",
            "charger",
            "keys",
        ]
        return any(marker in lowered for marker in markers)

    def _resolve_conversation_followup(self, request: InteractionRequest) -> InteractionRequest:
        # Resolve only short media follow-ups against the immediately preceding turn.
        # Never infer recipients, destructive operations, or send authorization.
        if not request.conversation_id:
            return request
        message = request.message.strip()
        if len(message.split()) > 8 or not self.agent_workflow._extract_media_action(message):
            return request
        if self.agent_workflow._parse_action_intent(message).kind != "unknown":
            return request
        history = self.conversations.get_history(request.user_id or "local-user", request.conversation_id)
        prior_user = next((turn for turn in reversed(history.messages) if turn.role == "user"), None)
        if not prior_user:
            return request
        prior_intent = self.agent_workflow._parse_action_intent(prior_user.content)
        if prior_intent.target_app not in {"Music", "Spotify"}:
            return request
        return request.model_copy(update={"message": f"{message} in {prior_intent.target_app}"})

    def _classify_request(
        self,
        message: str,
        visual_context: VisualContextPayload | None = None,
    ) -> tuple[RequestClassification, list[RequestRouteCandidate]]:
        stripped = message.strip()
        lowered = stripped.lower()

        if self.agent_workflow._is_non_action_message(message):
            stack = "visual" if visual_context and visual_context.image_path else self.llm._choose_text_stack(ChatRequest(message=message), visual_context)
            chosen = RequestClassification(
                mode="chat", stack=stack, confidence=0.95,
                reason="This is an explanatory question or explicitly negates an action, not permission to execute it.",
            )
            return chosen, [RequestRouteCandidate(**chosen.model_dump())]

        intent = self.agent_workflow._parse_action_intent(message)
        target_app = getattr(intent, "target_app", None)
        intent_name = intent.kind if intent and getattr(intent, "kind", "unknown") != "unknown" else None
        candidates: list[RequestRouteCandidate] = []

        if visual_context and visual_context.image_path and not intent_name:
            chosen = RequestClassification(
                mode="chat",
                stack="visual",
                intent=intent_name,
                target_app=target_app,
                confidence=0.92,
                reason="Visual context with an image path is present, so Cleo should use the visual stack.",
            )
            candidates.append(RequestRouteCandidate(**chosen.model_dump()))
            return chosen, candidates

        if intent_name:
            chosen = RequestClassification(
                mode="command",
                stack="action",
                intent=intent_name,
                target_app=target_app,
                confidence=0.9,
                reason="The request maps cleanly to a known action intent.",
            )
            candidates.append(RequestRouteCandidate(**chosen.model_dump()))
            chat_stack = self.llm._choose_text_stack(ChatRequest(message=message), visual_context)
            candidates.append(
                RequestRouteCandidate(
                    mode="chat",
                    stack=chat_stack,
                    intent=None,
                    target_app=None,
                    confidence=0.34,
                    reason="Fallback conversational interpretation if the action route fails.",
                )
            )
            return chosen, candidates

        if "?" in stripped and not any(
            token in lowered
            for token in ["inspect", "read", "list", "remember", "update", "set ", "create ", "plan ", "break down"]
        ):
            stack = self.llm._choose_text_stack(
                ChatRequest(message=message),
                visual_context,
            )
            chosen = RequestClassification(
                mode="chat",
                stack=stack,
                intent=None,
                target_app=None,
                confidence=0.72,
                reason="The request is phrased as a plain question without strong command markers.",
            )
            candidates.append(RequestRouteCandidate(**chosen.model_dump()))
            candidates.append(
                RequestRouteCandidate(
                    mode="command",
                    stack="action",
                    intent=None,
                    target_app=None,
                    confidence=0.22,
                    reason="Low-confidence action fallback in case the question was actually a command.",
                )
            )
            return self._finalize_request_classification(message, visual_context, chosen, candidates)

        imperative_markers = [
            "remember",
            "inspect",
            "read",
            "list",
            "update",
            "set ",
            "create ",
            "plan ",
            "break down",
            "summarize ",
            "analyze ",
            "find ",
            "open ",
        ]
        multi_step_markers = [" and ", " then ", " after that ", " followed by "]
        workspace_markers = [".md", ".py", ".tsx", ".ts", ".json", "repo", "workspace", "connector", "graph"]

        if any(marker in lowered for marker in imperative_markers):
            chosen = RequestClassification(
                mode="command",
                stack="action",
                intent=intent_name,
                target_app=target_app,
                confidence=0.7,
                reason="Imperative command markers were detected.",
            )
            candidates.append(RequestRouteCandidate(**chosen.model_dump()))
            candidates.append(
                RequestRouteCandidate(
                    mode="chat",
                    stack=self.llm._choose_text_stack(ChatRequest(message=message), visual_context),
                    intent=None,
                    target_app=None,
                    confidence=0.3,
                    reason="Fallback conversational interpretation.",
                )
            )
            return self._finalize_request_classification(message, visual_context, chosen, candidates)
        if any(marker in lowered for marker in multi_step_markers):
            chosen = RequestClassification(
                mode="command",
                stack="action",
                intent=intent_name,
                target_app=target_app,
                confidence=0.68,
                reason="The request looks multi-step, which is more command-like than conversational.",
            )
            candidates.append(RequestRouteCandidate(**chosen.model_dump()))
            candidates.append(
                RequestRouteCandidate(
                    mode="chat",
                    stack=self.llm._choose_text_stack(ChatRequest(message=message), visual_context),
                    intent=None,
                    target_app=None,
                    confidence=0.28,
                    reason="Fallback conversational interpretation.",
                )
            )
            return self._finalize_request_classification(message, visual_context, chosen, candidates)
        if any(marker in lowered for marker in workspace_markers):
            chosen = RequestClassification(
                mode="command",
                stack="action",
                intent=intent_name,
                target_app=target_app,
                confidence=0.66,
                reason="Workspace or code markers were detected.",
            )
            candidates.append(RequestRouteCandidate(**chosen.model_dump()))
            candidates.append(
                RequestRouteCandidate(
                    mode="chat",
                    stack="full",
                    intent=None,
                    target_app=None,
                    confidence=0.26,
                    reason="Fallback conversational interpretation.",
                )
            )
            return self._finalize_request_classification(message, visual_context, chosen, candidates)
        stack = self.llm._choose_text_stack(
            ChatRequest(message=message),
            visual_context,
        )
        chosen = RequestClassification(
            mode="chat",
            stack=stack,
            intent=None,
            target_app=None,
            confidence=0.58,
            reason="No strong command markers were found, so Cleo treats this as chat.",
        )
        candidates.append(RequestRouteCandidate(**chosen.model_dump()))
        candidates.append(
            RequestRouteCandidate(
                mode="command",
                stack="action",
                intent=None,
                target_app=None,
                confidence=0.24,
                reason="Low-confidence command fallback.",
            )
        )
        return self._finalize_request_classification(message, visual_context, chosen, candidates)

    def _finalize_request_classification(
        self,
        message: str,
        visual_context: VisualContextPayload | None,
        chosen: RequestClassification,
        candidates: list[RequestRouteCandidate],
    ) -> tuple[RequestClassification, list[RequestRouteCandidate]]:
        ranked = sorted(candidates, key=lambda candidate: candidate.confidence, reverse=True)
        should_model_assist = (
            0.45 <= chosen.confidence < 0.7
            and len(message.strip().split()) >= 4
            and visual_context is None
        )
        if should_model_assist:
            model_choice = self.llm.classify_request(
                message=message,
                candidates=ranked,
                visual_context=visual_context,
            )
            if model_choice is not None:
                chosen = model_choice
                ranked = sorted(
                    [
                        RequestRouteCandidate(**model_choice.model_dump()),
                        *[
                            candidate for candidate in ranked
                            if not (
                                candidate.mode == model_choice.mode
                                and candidate.stack == model_choice.stack
                                and (candidate.intent or None) == (model_choice.intent or None)
                                and (candidate.target_app or None) == (model_choice.target_app or None)
                            )
                        ],
                    ],
                    key=lambda candidate: candidate.confidence,
                    reverse=True,
                )
        return chosen, ranked

    def _fallback_interaction_reply(
        self,
        *,
        request: InteractionRequest,
        route_candidates: list[RequestRouteCandidate],
        current_classification: RequestClassification,
        enriched_message: str,
    ) -> InteractionReply | None:
        fallback = next(
            (
                candidate for candidate in route_candidates
                if candidate.mode != current_classification.mode or candidate.stack != current_classification.stack
            ),
            None,
        )
        if fallback is None or fallback.mode != "chat":
            return None
        chat_result = self.reply(
            ChatRequest(
                message=request.message,
                user_id=request.user_id,
                conversation_id=request.conversation_id or "auto-chat-fallback",
            ),
            model_message=enriched_message,
            visual_context=request.visual_context,
            classification=RequestClassification(
                mode=fallback.mode,
                stack=fallback.stack,
                intent=fallback.intent,
                target_app=fallback.target_app,
                confidence=fallback.confidence,
                reason=f"Fallback after action execution failed. {fallback.reason}",
            ),
        )
        return InteractionReply(
            mode="chat",
            conversation_id=chat_result.conversation_id,
            response=chat_result.reply,
            provider=chat_result.provider,
            model=chat_result.model,
            classification=RequestClassification(
                mode=fallback.mode,
                stack=fallback.stack,
                intent=fallback.intent,
                target_app=fallback.target_app,
                confidence=fallback.confidence,
                reason=f"Fallback after action execution failed. {fallback.reason}",
            ),
            route_candidates=route_candidates,
        )

    @staticmethod
    def _is_file_question(message: str) -> bool:
        return bool(re.search(r"\b(?:read|summarize|explain|what|find|search)\b", message, re.I)
                    and re.search(r"\b(?:my files|my documents|my pdf)\b|\.(?:pdf|docx|txt|md|rst|py|swift|js|ts|tsx|jsx|html|css|csv|tex)\b", message, re.I))

    def _classify_interaction(self, request: InteractionRequest):
        if request.visual_context is None and self._is_file_question(request.message):
            # Retrieved documents cannot turn a read-only question into permission for actions.
            return RequestClassification(mode="chat", stack="full", intent="file.qa", confidence=1.0,
                reason="Read-only file question; no action tools will execute."), []
        return self._classify_request(request.message, request.visual_context)

    def _feature_reply(self, message: str, user_id: str, conversation_id: str, *, allow_files: bool = True) -> InteractionReply | None:
        answer = feature_answer(message)
        normalized = message.lower().strip()
        if answer is None and re.search(r"\b(?:which model (?:are you|do you)|what model (?:are you|do you)|are you (?:running )?local|your runtime status|your capability status|what files can you (?:read|access))\b", normalized):
            status = self.get_capability_status()
            answer = (
                f"Routing: {status['routing_mode']}. Text model: {status['text_model']} "
                f"({'loaded' if status['text_loaded'] else 'not loaded in this process'}). "
                f"Visual model: {status['visual_model']} ({'loaded' if status['visual_loaded'] else 'not loaded in this process'}). "
                f"File access: {len(status['files']['roots'])} approved folders, {status['files']['indexed_files']} indexed files. "
                "I have not verified microphone, screen-recording, or Accessibility permissions from the backend."
            )
        if answer is None and allow_files and user_id == "local-user" and hasattr(self, "file_evidence"):
            asks_for_file = self._is_file_question(message)
            if asks_for_file and not self.file_evidence.retrieve(message):
                answer = "I couldn't retrieve matching file excerpts. Choose a folder in Pulse > Files, refresh the index, and give me a filename or topic. I won't guess what's in your files."
        if answer is None:
            return None
        self.conversations.append(user_id, conversation_id, "user", message)
        self.conversations.append(user_id, conversation_id, "assistant", answer)
        return InteractionReply(
            mode="chat", conversation_id=conversation_id, response=answer,
            provider="verified-product-facts", model="cleo-features",
            classification=RequestClassification(mode="chat", stack="compact", intent="feature.help", confidence=1.0,
                reason="Answer from verified Cleo feature facts, not model inference."),
        )

    def get_capability_status(self) -> dict:
        return {
            "routing_mode": self.settings.routing_mode,
            "text_model": self.settings.text_model_id,
            "visual_model": self.settings.local_model_id,
            "text_loaded": self.llm._text_model is not None,
            "visual_loaded": self.llm._smolvlm_model is not None,
            "files": self.file_evidence.snapshot(),
            "pulse": self.proactivity.handle({}),
            "native_permissions": "not verified by the backend",
            "limitations": ["No biometric sensing", "No unrestricted file access", "Drafting is not sending", "No universal device control"],
        }

    @staticmethod
    def _with_file_sources(response: str, context) -> str:
        if not response.strip() or not context.file_evidence:
            return response
        body = response.split("\n\nRetrieved sources:\n", 1)[0]
        links = []
        for source in context.file_evidence:
            label = source["title"].replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            links.append(f"[{label}]({Path(source['path']).as_uri()})")
        return body + "\n\nRetrieved sources:\n" + "\n".join(links)

    def _graph_memory_for_request(self, message: str, history: ConversationHistory, user_id: str) -> list[str]:
        # Resolve terse follow-ups against the last user turn without diluting new requests.
        query = message
        if re.fullmatch(r"\s*(why(?: not)?|how so|tell me more|what about (?:it|that)|and then)[?.! ]*", message, re.I):
            previous = next((item.content for item in reversed(history.messages)
                             if item.role == "user" and item.content.strip() != message.strip()), "")
            query = f"{previous[:400]} {message}"
        return self.brain_graph.relevant_summary(query, user_id=user_id)

    def _review_response(
        self,
        *,
        original_message: str,
        draft: str,
        context: str,
        user_id: str | None,
        conversation_id: str,
        include_files: bool = True,
    ) -> str:
        user_key = user_id or "local-user"
        profile = self.memory.get_profile(user_key)
        graph = self.brain_graph.get_graph()
        history = self.conversations.get_history(user_key, conversation_id)
        review_context = self.context_builder.build(
            user_id=user_key,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=self._graph_memory_for_request(original_message, history, user_key),
            query=original_message if include_files else "",
        )

        critic_prompt = (
            "You are Cleo's critic specialist. Review the draft answer for accuracy, relevance, and unnecessary filler. "
            "Reply with 1-3 short bullet points describing only the biggest issues. If the draft is already solid, say 'Looks good.'\n\n"
            f"User request: {original_message}\n"
            f"Context: {context}\n"
            f"Draft: {draft}"
        )
        writer_prompt_template = (
            "You are Cleo's writer specialist. Rewrite the draft into the best final answer for the user. "
            "Be concise, concrete, and fix issues raised by the critic.\n\n"
            f"User request: {original_message}\n"
            "Critic notes:\n{critic}\n\n"
            f"Draft:\n{draft}"
        )
        try:
            critic = self.llm.chat(
                ChatRequest(
                    message=critic_prompt,
                    user_id=user_id,
                    conversation_id=conversation_id,
                ),
                review_context,
                history,
            ).content
            final = self.llm.chat(
                ChatRequest(
                    message=writer_prompt_template.format(critic=critic),
                    user_id=user_id,
                    conversation_id=conversation_id,
                ),
                review_context,
                history,
            ).content
            return self._with_file_sources(final.strip() or draft, review_context)
        except LLMError:
            return draft

    def _extract_chatgpt_messages(self, item: dict) -> list[tuple[str, str]]:
        mapping = item.get("mapping")
        if isinstance(mapping, dict):
            extracted: list[tuple[str, str]] = []
            nodes = []
            current = item.get("current_node")
            if isinstance(current, str) and current in mapping:
                seen = set()
                while isinstance(current, str) and current in mapping and current not in seen:
                    seen.add(current)
                    node = mapping[current]
                    if not isinstance(node, dict):
                        break
                    nodes.append(node)
                    current = node.get("parent")
                nodes.reverse()
            else:
                nodes = [node for node in mapping.values() if isinstance(node, dict) and isinstance(node.get("message"), dict)]
                nodes.sort(key=lambda node: ((node.get("message") or {}).get("create_time") or 0))
            for node in nodes:
                message = node.get("message") or {}
                if not isinstance(message, dict):
                    continue
                author = (message.get("author") or {}).get("role")
                content = message.get("content") or {}
                if not isinstance(content, dict):
                    continue
                parts = content.get("parts") or []
                text = "\n".join(part for part in parts if isinstance(part, str)).strip()
                if author in {"user", "assistant"} and text:
                    extracted.append((author, text))
            return extracted

        messages = item.get("messages")
        if isinstance(messages, list):
            extracted = []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = message.get("role") or message.get("author")
                content = message.get("content")
                text = content.strip() if isinstance(content, str) else ""
                if role in {"user", "assistant"} and text:
                    extracted.append((role, text))
            return extracted
        return []

    def _seed_defaults(self) -> None:
        if any(routine.name.lower() == "research mode" for routine in self.routines.list_routines("local-user")):
            return
        self.routines.upsert(
            "local-user",
            name="Research Mode",
            trigger="when I'm in research mode",
            instructions=(
                "Collect useful links, summarize the strongest sources into memory, "
                "and keep a concise running context of findings."
            ),
            source="system",
        )

    def _app_adapters(self) -> list[AppAdapter]:
        return [
            AppAdapter(
                key="arc",
                app_name="Arc",
                description="Browser-aware actions for opening sites, focusing tabs, and using visual web context.",
                actions=["open site", "switch tab", "focus current tab", "capture web context"],
            ),
            AppAdapter(
                key="mail",
                app_name="Mail",
                description="Email drafting and send-oriented routines for Apple Mail.",
                actions=["draft email", "reply to email", "open inbox"],
            ),
            AppAdapter(
                key="notes",
                app_name="Notes",
                description="Notes capture, recall, and highlighted-text context from Apple Notes.",
                actions=["open note", "capture selection", "search recent notes"],
            ),
            AppAdapter(
                key="finder",
                app_name="Finder",
                description="File-aware actions for opening, moving, and collecting local documents.",
                actions=["open path", "move file", "reveal recent document", "build context pack"],
            ),
            AppAdapter(
                key="calendar",
                app_name="Calendar",
                description="Calendar event planning and scheduling actions.",
                actions=["create event", "open calendar", "prepare schedule context"],
            ),
            AppAdapter(
                key="whatsapp",
                app_name="WhatsApp",
                description="Message-oriented adapter for opening chats and drafting replies.",
                actions=["open app", "focus conversation", "draft response"],
            ),
            AppAdapter(
                key="slack",
                app_name="Slack",
                description="Workspace messaging adapter for channel context and reply drafting.",
                actions=["open workspace", "draft message", "summarize channel context"],
            ),
            AppAdapter(
                key="vscode",
                app_name="Visual Studio Code",
                description="Codebase-aware adapter for focusing projects, files, and delegating coding tasks.",
                actions=["open project", "open file", "delegate to Codex", "build codebase context pack"],
            ),
            AppAdapter(
                key="terminal",
                app_name="Terminal",
                description="Shell and automation adapter for commands, scripts, and diagnostics.",
                actions=["open terminal", "run shortcut", "execute command workflow"],
            ),
        ]

    def _default_devices(self) -> list[dict]:
        return [
            {
                "device_id": "device-cleo-hub",
                "name": "Cleo Hub",
                "device_type": "primary-mac",
                "hostname": "cleo-hub.local",
                "status": "available",
                "trust_state": "trusted",
                "protocols": ["local-bridge", "http"],
                "capabilities": [
                    {"family": "desktop", "operations": ["apps", "files", "voice", "screen"], "transport": "local-bridge"},
                    {"family": "agent", "operations": ["chat", "command-routing", "memory"], "transport": "http"},
                ],
                "notes": "Primary Cleo machine and orchestration hub.",
            },
            {
                "device_id": "device-secondary-laptop",
                "name": "Secondary Laptop",
                "device_type": "laptop-node",
                "hostname": "cleo-node.local",
                "status": "discovered",
                "trust_state": "trusted",
                "protocols": ["ssh", "http"],
                "capabilities": [
                    {"family": "compute", "operations": ["remote-command", "file-sync", "render-jobs"], "transport": "ssh"},
                ],
                "notes": "Useful as a remote compute or automation node on the same Wi-Fi.",
            },
            {
                "device_id": "device-pi-zero",
                "name": "Raspberry Pi Node",
                "device_type": "edge-node",
                "hostname": "cleo-pi.local",
                "status": "discovered",
                "trust_state": "trusted",
                "protocols": ["ssh", "http", "mqtt"],
                "capabilities": [
                    {"family": "sensor", "operations": ["camera-feed", "gpio", "edge-automation"], "transport": "http"},
                ],
                "notes": "Edge node for cameras, sensors, or lightweight automation tasks.",
            },
        ]

    def _sync_device_graph(self, user_id: str, device: LANDevice) -> None:
        node_id = f"device:{device.device_id}"
        protocols = ", ".join(device.protocols) or "none"
        self.brain_graph.ensure_node(
            node_id=node_id,
            label=device.name,
            kind="device",
            group="environment",
            metadata={
                "device_type": device.device_type,
                "hostname": device.hostname or "",
                "ip_address": device.ip_address or "",
                "status": device.status,
                "trust_state": device.trust_state,
                "protocols": protocols,
            },
        )
        self.brain_graph.ensure_edge(
            source="assistant:cleo",
            target=node_id,
            relation="can_orchestrate",
            strength=0.92 if device.trust_state == "trusted" else 0.5,
        )
        self.brain_graph.ensure_edge(
            source=f"user:{user_id}",
            target=node_id,
            relation="has_device",
            strength=0.88,
        )

    def _record_timeline_event(
        self,
        *,
        user_id: str,
        event_type: str,
        title: str,
        detail: str | None = None,
        visual_context: VisualContextPayload | None = None,
        command_reply: CommandReply | None = None,
    ) -> None:
        app_name, file_paths = self._infer_context_handles(title, visual_context)
        metadata: dict[str, str] = {}
        if visual_context is not None:
            metadata["visual_source"] = visual_context.source
        if command_reply is not None:
            metadata["tasks"] = str(len(command_reply.tasks))
            metadata["specialists"] = str(len({task.specialist for task in command_reply.tasks}))
        self.timeline.record(
            user_id,
            event_type=event_type,
            title=title[:140],
            detail=detail,
            app_name=app_name,
            file_paths=file_paths,
            metadata=metadata,
        )

    def _update_session_memory(
        self,
        *,
        user_id: str,
        goal: str | None,
        visual_context: VisualContextPayload | None,
        active_tasks: list[str] | None = None,
        active_files: list[str] | None = None,
        last_context_pack_id: str | None = None,
    ) -> SessionMemory:
        active_app, inferred_files = self._infer_context_handles(goal or "", visual_context)
        combined_files = active_files if active_files is not None else inferred_files
        return self.sessions.update(
            user_id,
            active_goal=goal,
            active_app=active_app,
            active_files=combined_files,
            active_tasks=active_tasks or [],
            last_context_pack_id=last_context_pack_id,
        )

    def _update_session_memory_for_command(
        self,
        *,
        user_id: str,
        command: str,
        result: CommandReply,
    ) -> SessionMemory:
        tasks = [task.title for task in result.tasks]
        active_files = self._extract_paths_from_text(
            "\n".join([command, result.summary or "", result.final_response])
        )
        active_app = self._guess_app_from_text(command)
        return self.sessions.update(
            user_id,
            active_goal=command,
            active_app=active_app,
            active_files=active_files,
            active_tasks=tasks,
        )

    def _infer_context_handles(
        self,
        message: str,
        visual_context: VisualContextPayload | None,
    ) -> tuple[str | None, list[str]]:
        app_name = self._guess_app_from_text(message)
        file_paths = self._extract_paths_from_text(message)
        if visual_context:
            if visual_context.summary:
                app_name = app_name or self._guess_app_from_text(visual_context.summary)
                file_paths.extend(self._extract_paths_from_text(visual_context.summary))
            if visual_context.image_path:
                file_paths.append(visual_context.image_path)
        deduped = list(dict.fromkeys(path for path in file_paths if path))
        return app_name, deduped[:12]

    def _guess_app_from_text(self, text: str) -> str | None:
        lowered = text.lower()
        for adapter in self._app_adapters():
            if adapter.key in lowered or adapter.app_name.lower() in lowered:
                return adapter.app_name
        return None

    @staticmethod
    def _extract_paths_from_text(text: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"/[^\s\"']+", text)))

    def _build_context_pack(
        self,
        user_id: str,
        root: Path,
        title: str | None,
    ) -> ContextPack:
        if not root.exists():
            raise FileNotFoundError(f"Context pack source does not exist: {root}")

        if root.is_dir():
            file_paths = [
                str(path)
                for path in root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix.lower() not in {".pyc", ".pyo"}
                and not any(part.startswith(".") for part in path.parts[-3:])
            ]
        else:
            file_paths = [str(root)]

        limited_paths = file_paths[:80]
        suffix_counts: dict[str, int] = {}
        for file_path in limited_paths:
            suffix = Path(file_path).suffix.lower() or "[none]"
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        top_suffixes = sorted(suffix_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        summary_bits = [f"{len(file_paths)} files"]
        if top_suffixes:
            summary_bits.append(
                "top types: " + ", ".join(f"{suffix} ({count})" for suffix, count in top_suffixes)
            )
        source_type = self._context_pack_source_type(root, file_paths)
        pack_title = title or root.name or "Context Pack"
        pack = self.context_packs.add_pack(
            user_id,
            title=pack_title,
            source_type=source_type,
            root_path=str(root),
            summary="; ".join(summary_bits),
            file_paths=limited_paths,
        )
        return pack

    @staticmethod
    def _context_pack_source_type(root: Path, file_paths: list[str]) -> str:
        if root.is_file():
            suffix = root.suffix.lower()
            if suffix == ".pdf":
                return "pdf"
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".heic"}:
                return "image"
            if suffix in {".py", ".ts", ".tsx", ".js", ".swift", ".rs", ".go", ".java"}:
                return "code-file"
            return "file"
        suffixes = {Path(path).suffix.lower() for path in file_paths}
        if suffixes and suffixes.issubset({".png", ".jpg", ".jpeg", ".webp", ".heic"}):
            return "image-set"
        if suffixes & {".py", ".ts", ".tsx", ".js", ".swift", ".rs", ".go", ".java", ".json"}:
            return "codebase"
        return "folder"

    def _slugify(self, text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return slug or "conversation"
