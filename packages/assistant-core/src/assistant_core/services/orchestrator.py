import json
import re
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator

from assistant_core.connectors.registry import ConnectorRegistry
from assistant_core.config import get_settings
from assistant_core.models import (
    BrainGraph,
    ChatReply,
    ChatRequest,
    ChatGPTImportReply,
    ChatGPTImportRequest,
    CommandReply,
    CommandRequest,
    ConnectorSummary,
    ConversationHistory,
    ImportHistoryEntry,
    InteractionReply,
    InteractionRequest,
    UserProfile,
    UserProfileUpdate,
    VisualContextPayload,
)
from assistant_core.services.agent_workflow import AgentWorkflowService
from assistant_core.services.context_builder import ContextBuilder
from assistant_core.services.llm import LLMError, RoutingLLMService
from assistant_core.services.memory_extractor import MemoryExtractor
from assistant_core.services.memory import (
    InMemoryBrainGraphStore,
    InMemoryConversationStore,
    InMemoryProfileStore,
    PersistentStateBackend,
)
from assistant_core.services.tool_registry import CommandToolRegistry


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
        self.import_history = self.state_backend.load_import_history()
        self.context_builder = ContextBuilder()
        self.memory_extractor = MemoryExtractor(self.memory, self.brain_graph)
        self.llm = RoutingLLMService(self.settings)
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

    def reply(
        self,
        request: ChatRequest,
        model_message: str | None = None,
        visual_context: VisualContextPayload | None = None,
    ) -> ChatReply:
        user_id = request.user_id or "local-user"
        conversation_id = request.conversation_id or "default"
        profile = self.memory_extractor.ingest_user_message(user_id, request.message)
        profile = self.memory.get_profile(user_id)
        app_name = profile.display_name or "Cleo"
        graph = self.brain_graph.get_graph()
        history = self.conversations.get_history(user_id, conversation_id)
        graph_summary = self.brain_graph.relevant_summary(model_message or request.message)
        context = self.context_builder.build(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=graph_summary,
        )

        next_steps = [
            "Add your first real app connectors, such as Google, Notion, and GitHub.",
            "Expose the brain graph in the mobile app as an interactive network view.",
            "Persist memory and graph state beyond the in-memory scaffold.",
        ]

        try:
            llm_reply = self.llm.chat(
                ChatRequest(
                    message=model_message or request.message,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                ),
                context,
                history,
                visual_context,
            )
            reply = llm_reply.content
            self.conversations.append(user_id, conversation_id, "user", request.message)
            self.conversations.append(user_id, conversation_id, "assistant", reply)
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

        used_connectors = []
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

    def get_model_status(self) -> dict[str, str]:
        return self.llm.check_health()

    def interact(self, request: InteractionRequest) -> InteractionReply:
        mode = self._classify_interaction_mode(request.message)
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
                )
            return InteractionReply(
                mode="command",
                conversation_id=result.conversation_id,
                response=result.final_response,
                provider=result.provider,
                model=result.model,
                summary=result.summary,
                tasks=result.tasks,
                executions=result.executions,
            )

        result = self.reply(
            ChatRequest(
                message=request.message,
                user_id=request.user_id,
                conversation_id=request.conversation_id or "auto-chat",
            ),
            model_message=enriched_message,
            visual_context=request.visual_context,
        )
        response_text = result.reply
        if request.response_mode == "reviewed" and result.provider:
            response_text = self._review_response(
                original_message=request.message,
                draft=result.reply,
                context=enriched_message,
                user_id=request.user_id,
                conversation_id=result.conversation_id,
            )
        return InteractionReply(
            mode="chat",
            conversation_id=result.conversation_id,
            response=response_text,
            provider=result.provider,
            model=result.model,
        )

    def stream_interaction_events(self, request: InteractionRequest) -> Iterator[dict]:
        mode = self._classify_interaction_mode(request.message)
        enriched_message = self._merge_visual_context(request.message, request.visual_context)
        yield {"type": "meta", "mode": mode}

        if mode != "command":
            result = self.reply(
                ChatRequest(
                    message=request.message,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id or "auto-chat",
                ),
                model_message=enriched_message,
                visual_context=request.visual_context,
            )
            response_text = result.reply
            if request.response_mode == "reviewed" and result.provider:
                response_text = self._review_response(
                    original_message=request.message,
                    draft=result.reply,
                    context=enriched_message,
                    user_id=request.user_id,
                    conversation_id=result.conversation_id,
                )
            yield {
                "type": "final",
                "mode": "chat",
                "conversation_id": result.conversation_id,
                "response": response_text,
                "provider": result.provider,
                "model": result.model,
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
        graph_summary = self.brain_graph.relevant_summary(command_for_model)
        context = self.context_builder.build(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=graph_summary,
        )

        command_request = CommandRequest(
            command=command_for_model,
            user_id=request.user_id,
            conversation_id=conversation_id,
        )
        tasks = self.agent_workflow.plan(command_for_model)
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
        final_response = llm_reply.content
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
            )

        self.conversations.append(user_id, conversation_id, "user", request.message)
        self.conversations.append(user_id, conversation_id, "assistant", final_response)

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
        graph_summary = self.brain_graph.relevant_summary(command_for_model)
        context = self.context_builder.build(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=graph_summary,
        )

        result = self.agent_workflow.execute(
            CommandRequest(
                command=command_for_model,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
            ),
            context=context,
            history=history,
        )

        self.conversations.append(user_id, conversation_id, "user", request.command)
        self.conversations.append(user_id, conversation_id, "assistant", result.final_response)
        return result

    def import_chatgpt_export(self, request: ChatGPTImportRequest) -> ChatGPTImportReply:
        user_id = request.user_id or "local-user"
        path = Path(request.file_path).expanduser().resolve()
        data = json.loads(path.read_text())
        conversations = data if isinstance(data, list) else data.get("conversations", [])

        imported_conversations = 0
        imported_messages = 0
        imported_user_messages = 0

        for index, item in enumerate(conversations):
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
                metadata={"source": "chatgpt-export"},
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
        if visual_context.source == "pointer-context":
            pointer_instruction = (
                "No explicit text selection was detected. Use the attached pointer-centered image and nearby OCR as broader visual context, "
                "but still prioritize the center of the captured region over surrounding page furniture.\n"
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
        profile = self.memory_extractor.ingest_user_message(user_id, request.message)
        graph = self.brain_graph.get_graph()
        history = self.conversations.get_history(user_id, conversation_id)
        context = self.context_builder.build(
            user_id=user_id,
            conversation_id=conversation_id,
            profile=profile,
            history=history,
            graph=graph,
            graph_summary=self.brain_graph.relevant_summary(request.message),
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

        reply = "".join(chunks).strip()
        if reply:
            self.conversations.append(user_id, conversation_id, "user", request.message)
            self.conversations.append(user_id, conversation_id, "assistant", reply)

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

    def _classify_interaction_mode(self, message: str) -> str:
        stripped = message.strip()
        lowered = stripped.lower()

        if "?" in stripped and not any(
            token in lowered
            for token in ["inspect", "read", "list", "remember", "update", "set ", "create ", "plan ", "break down"]
        ):
            return "chat"

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
            return "command"
        if any(marker in lowered for marker in multi_step_markers):
            return "command"
        if any(marker in lowered for marker in workspace_markers):
            return "command"
        return "chat"

    def _review_response(
        self,
        *,
        original_message: str,
        draft: str,
        context: str,
        user_id: str | None,
        conversation_id: str,
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
            graph_summary=self.brain_graph.relevant_summary(original_message),
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
            return final.strip() or draft
        except LLMError:
            return draft

    def _extract_chatgpt_messages(self, item: dict) -> list[tuple[str, str]]:
        mapping = item.get("mapping")
        if isinstance(mapping, dict):
            extracted: list[tuple[str, str]] = []
            for node in mapping.values():
                message = node.get("message") or {}
                author = (message.get("author") or {}).get("role")
                content = message.get("content") or {}
                parts = content.get("parts") or []
                text = "\n".join(part for part in parts if isinstance(part, str)).strip()
                if author in {"user", "assistant"} and text:
                    extracted.append((author, text))
            return extracted

        messages = item.get("messages")
        if isinstance(messages, list):
            extracted = []
            for message in messages:
                role = message.get("role") or message.get("author")
                text = (message.get("content") or "").strip()
                if role in {"user", "assistant"} and text:
                    extracted.append((role, text))
            return extracted
        return []

    def _slugify(self, text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return slug or "conversation"
