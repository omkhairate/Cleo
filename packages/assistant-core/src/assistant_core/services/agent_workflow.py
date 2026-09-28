from dataclasses import dataclass
import re
from datetime import datetime, timedelta

from assistant_core.models import (
    AgentExecution,
    AgentTask,
    AssistantContext,
    ChatRequest,
    CommandReply,
    CommandRequest,
    ConversationHistory,
    ToolCallRecord,
)
from assistant_core.services.llm import LLMError, LLMReply, RoutingLLMService
from assistant_core.services.memory_extractor import MemoryExtractor
from assistant_core.services.tool_registry import CommandToolRegistry


@dataclass
class PlannedTask:
    title: str
    description: str
    specialist: str
    tool_names: list[str]


@dataclass
class ActionIntent:
    kind: str
    target_app: str | None = None
    query: str | None = None
    direction: str | None = None
    media_action: str | None = None
    recipient: str | None = None
    subject: str | None = None
    body: str | None = None
    shortcut_name: str | None = None
    source_path: str | None = None
    destination_path: str | None = None
    calendar_title: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    codex_prompt: str | None = None


class AgentWorkflowService:
    """Breaks commands into specialist tasks and executes them with tools."""

    def __init__(
        self,
        llm: RoutingLLMService,
        tool_registry: CommandToolRegistry,
        memory_extractor: MemoryExtractor,
    ) -> None:
        self.llm = llm
        self.tool_registry = tool_registry
        self.memory_extractor = memory_extractor

    def execute(
        self,
        request: CommandRequest,
        *,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> CommandReply:
        user_id = request.user_id or "local-user"
        conversation_id = request.conversation_id or "command"

        tasks = self.plan(request.command)

        executions: list[AgentExecution] = []
        for task in tasks:
            execution = self._execute_task(
                task,
                user_id=user_id,
                conversation_id=conversation_id,
                context=context,
                history=history,
            )
            task.status = execution.status
            task.output = execution.output
            executions.append(execution)

        llm_reply = self._compose_final_response(
            request=request,
            context=context,
            history=history,
            tasks=tasks,
            executions=executions,
        )

        summary = (
            f"Planned {len(tasks)} tasks across "
            f"{len({task.specialist for task in tasks})} specialist roles."
        )

        return CommandReply(
            command=request.command,
            conversation_id=conversation_id,
            summary=summary,
            final_response=llm_reply.content,
            provider=llm_reply.provider,
            model=llm_reply.model,
            tasks=tasks,
            executions=executions,
        )

    def plan(self, command: str) -> list[AgentTask]:
        planned_tasks = self._plan_tasks(command)
        return [
            AgentTask(
                task_id=f"task-{index + 1}",
                title=task.title,
                description=task.description,
                specialist=task.specialist,
                tool_names=task.tool_names,
            )
            for index, task in enumerate(planned_tasks)
        ]

    def execute_one(
        self,
        task: AgentTask,
        *,
        user_id: str,
        conversation_id: str,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> AgentExecution:
        return self._execute_task(
            task,
            user_id=user_id,
            conversation_id=conversation_id,
            context=context,
            history=history,
        )

    def compose_final(
        self,
        *,
        request: CommandRequest,
        context: AssistantContext,
        history: ConversationHistory,
        tasks: list[AgentTask],
        executions: list[AgentExecution],
    ) -> LLMReply:
        return self._compose_final_response(
            request=request,
            context=context,
            history=history,
            tasks=tasks,
            executions=executions,
        )

    def can_compose_direct_final(
        self,
        *,
        tasks: list[AgentTask],
        executions: list[AgentExecution],
    ) -> bool:
        if not tasks or not executions or len(tasks) != len(executions):
            return False
        if any(task.specialist != "action" for task in tasks):
            return False
        return all(execution.status in {"completed", "blocked"} for execution in executions)

    def compose_direct_final(
        self,
        *,
        tasks: list[AgentTask],
        executions: list[AgentExecution],
    ) -> LLMReply:
        lines: list[str] = []
        for task, execution in zip(tasks, executions, strict=False):
            if execution.status == "completed":
                lines.append(self._direct_success_line(task, execution))
            else:
                lines.append(self._direct_blocked_line(task, execution))
        content = " ".join(line.strip() for line in lines if line.strip()) or "Done."
        return LLMReply(
            content=content,
            provider="deterministic",
            model="direct-action-summary",
        )

    def _plan_tasks(self, command: str) -> list[PlannedTask]:
        chunks = self._split_command(command)
        planned: list[PlannedTask] = []
        for chunk in chunks:
            specialist = self._pick_specialist(chunk)
            planned.append(
                PlannedTask(
                    title=self._title_for_chunk(chunk),
                    description=chunk,
                    specialist=specialist,
                    tool_names=self._tool_names_for_specialist(specialist),
                )
            )
        if not planned:
            planned.append(
                PlannedTask(
                    title="Handle command",
                    description=command,
                    specialist="writer",
                    tool_names=[],
                )
            )
        return planned

    def _split_command(self, command: str) -> list[str]:
        if self._parse_action_intent(command).kind in {"email.compose", "messaging.draft", "codex.delegate"}:
            return [command.strip()]
        if re.search(
            r"\bopen\b.*\btab\b.*\band\b.*\b(?:look for|search for|google)\b",
            command,
            re.IGNORECASE,
        ):
            return [command.strip()]
        normalized = command.replace(" then ", " and ")
        pieces = [
            piece.strip(" ,.")
            for piece in normalized.split(" and ")
            if piece.strip(" ,.")
        ]
        return pieces or [command.strip()]

    def _pick_specialist(self, chunk: str) -> str:
        if self._is_non_action_message(chunk):
            return "writer"
        lowered = chunk.lower()
        if any(token in lowered for token in ["remember", "prefer", "call me", "profile", "preference"]):
            return "memory"
        if any(token in lowered for token in ["open ", "launch ", "start ", "open app", "application", "go to ", "focus ", "switch to ", "bring up ", "find "]):
            return "action"
        if any(
            token in lowered
            for token in [
                "search for",
                "look for",
                "google ",
                "pause",
                "play",
                "resume",
                "stop",
                "youtube",
                "video",
                "browser",
                "tab",
                "stream",
                "spotify",
                "music app",
                "next track",
                "previous track",
                "mail",
                "email",
                "slack",
                "whatsapp",
                "notes",
                "message",
                "chat",
                "codex",
                "calendar",
                "event",
                "meeting",
                "shortcut",
                "shortcuts",
                "move ",
                "rename ",
                "finder",
                "switch to",
            ]
        ):
            return "action"
        if any(token in lowered for token in ["connector", "integrations", "apps", "graph"]):
            return "connector"
        if any(token in lowered for token in ["file", "workspace", "repo", "read", "inspect code"]):
            return "workspace"
        if any(token in lowered for token in ["plan", "break down", "steps", "roadmap"]):
            return "planner"
        return "writer"

    def _tool_names_for_specialist(self, specialist: str) -> list[str]:
        if specialist == "memory":
            return ["get_profile", "set_preference"]
        if specialist == "connector":
            return ["list_connectors", "get_graph_summary"]
        if specialist == "workspace":
            return ["list_workspace_files", "read_workspace_file"]
        if specialist == "planner":
            return ["get_profile", "get_conversation_history"]
        if specialist == "action":
            return [
                "open_application",
                "activate_application",
                "open_path",
                "control_youtube_playback",
                "control_browser_media",
                "control_media_app",
                "compose_email_draft",
                "run_shortcut",
                "create_calendar_event",
                "move_file",
                "switch_browser_tab",
                "focus_browser_tab",
                "open_browser_search",
                "search_spotlight_applications",
                "search_spotlight_files",
                "search_recent_documents",
                "delegate_to_codex",
                "get_relevant_graph_summary",
            ]
        return ["get_profile"]

    def _title_for_chunk(self, chunk: str) -> str:
        if self._pick_specialist(chunk) == "action":
            intent = self._parse_action_intent(chunk)
            if intent.kind == "browser.search" and intent.query:
                return f"Search Browser For {intent.query[:28]}".strip()
            if intent.kind == "notes.search" and intent.query:
                return f"Search Notes For {intent.query[:24]}".strip()
            if intent.kind == "messaging.draft":
                return "Draft Message"
            if intent.kind == "messaging.open" and intent.target_app:
                return f"Open {intent.target_app}"
            if intent.kind in {"app.open", "app.activate"} and intent.target_app:
                return f"Open {intent.target_app}"
            if intent.kind == "browser.focus_tab" and intent.query:
                return f"Focus Tab {intent.query[:28]}".strip()
            if intent.kind == "browser.switch_tab" and intent.direction:
                return f"Switch To {intent.direction.title()} Tab"
            if intent.kind == "email.compose":
                return "Draft Email"
            if intent.kind == "calendar.create" and intent.calendar_title:
                return f"Create Event {intent.calendar_title[:24]}".strip()
            if intent.kind == "document.open" and intent.query:
                return f"Open Document {intent.query[:22]}".strip()
            if intent.kind == "document.open_recent" and intent.query:
                return f"Open Recent {intent.query[:24]}".strip()
            if intent.kind == "shortcut.run" and intent.shortcut_name:
                return f"Run Shortcut {intent.shortcut_name[:20]}".strip()
            if intent.kind == "file.move" and intent.source_path:
                return f"Move {intent.source_path.split('/')[-1][:24]}".strip()
            if intent.kind == "codex.delegate":
                return "Delegate To Codex"
        words = chunk.split()
        if not words:
            return "Handle task"
        return " ".join(words[:6]).strip().capitalize()

    def _execute_task(
        self,
        task: AgentTask,
        *,
        user_id: str,
        conversation_id: str,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> AgentExecution:
        if task.specialist == "memory":
            return self._run_memory_task(task, user_id=user_id, context=context, history=history)
        if task.specialist == "connector":
            return self._run_connector_task(task, context=context, history=history)
        if task.specialist == "workspace":
            return self._run_workspace_task(task, context=context, history=history)
        if task.specialist == "planner":
            return self._run_planner_task(
                task,
                user_id=user_id,
                conversation_id=conversation_id,
                context=context,
                history=history,
            )
        if task.specialist == "action":
            return self._run_action_task(task, context=context, history=history)
        return self._run_writer_task(task, user_id=user_id, context=context, history=history)

    def _run_memory_task(
        self,
        task: AgentTask,
        *,
        user_id: str,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> AgentExecution:
        tool_calls: list[ToolCallRecord] = []
        profile, profile_call = self.tool_registry.get_profile(user_id)
        before_preferences = len(profile.preferences)
        before_workflows = len(profile.workflows)
        tool_calls.append(profile_call)
        updated_profile = self.memory_extractor.ingest_user_message(user_id, task.description)
        if (
            len(updated_profile.preferences) != before_preferences
            or len(updated_profile.workflows) != before_workflows
        ):
            tool_calls.append(
                ToolCallRecord(
                    tool_name="memory_extractor",
                    result_summary="Applied inferred memory updates from the task description.",
                )
            )
        raw_output = (
            f"Profile now has {len(updated_profile.preferences)} preferences and "
            f"{len(updated_profile.workflows)} workflows."
        )
        return AgentExecution(
            task_id=task.task_id,
            specialist=task.specialist,
            status="completed",
            reasoning="This task looked like a user-memory or preference update.",
            tool_calls=tool_calls,
            output=self._specialist_handoff(
                task=task,
                raw_output=raw_output,
                context=context,
                history=history,
                fallback=raw_output,
            ),
        )

    def _run_connector_task(
        self,
        task: AgentTask,
        *,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> AgentExecution:
        connectors, connectors_call = self.tool_registry.list_connectors()
        graph_summary, graph_call = self.tool_registry.get_relevant_graph_summary(task.description)
        raw_output = (
            f"Available connector domains: {', '.join(connectors)}. "
            f"Graph summary: {'; '.join(graph_summary[:4])}."
        )
        return AgentExecution(
            task_id=task.task_id,
            specialist=task.specialist,
            status="completed",
            reasoning="This task needed integration visibility and graph context.",
            tool_calls=[connectors_call, graph_call],
            output=self._specialist_handoff(
                task=task,
                raw_output=raw_output,
                context=context,
                history=history,
                fallback=raw_output,
            ),
        )

    def _run_action_task(
        self,
        task: AgentTask,
        *,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> AgentExecution:
        tool_calls: list[ToolCallRecord] = []
        graph_summary, graph_call = self.tool_registry.get_relevant_graph_summary(task.description)
        tool_calls.append(graph_call)
        lowered = task.description.lower()
        intent = self._parse_action_intent(task.description)
        if intent.kind == "codex.delegate" and intent.codex_prompt:
            try:
                codex_call = self.tool_registry.execute_capability(
                    "codex",
                    "delegate",
                    user_id=context.user_id,
                    requested_app="Terminal",
                    prompt=intent.codex_prompt,
                )
                tool_calls.append(codex_call)
                raw_output = (
                    f"Delegated to Codex with prompt: {intent.codex_prompt[:160]}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not delegate to Codex: {exc}"
                status = "blocked"
        elif intent.kind == "shortcut.run" and intent.shortcut_name:
            try:
                shortcut_call = self.tool_registry.run_shortcut(intent.shortcut_name)
                tool_calls.append(shortcut_call)
                raw_output = (
                    f"Ran the Shortcut {intent.shortcut_name}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not run the Shortcut {intent.shortcut_name}: {exc}"
                status = "blocked"
        elif intent.kind == "calendar.create" and intent.calendar_title and intent.start_at and intent.end_at:
            try:
                calendar_call = self.tool_registry.execute_capability(
                    "calendar",
                    "create",
                    user_id=context.user_id,
                    requested_app=intent.target_app or "Calendar",
                    title=intent.calendar_title,
                    start_at=intent.start_at,
                    end_at=intent.end_at,
                )
                tool_calls.append(calendar_call)
                raw_output = (
                    f"Created calendar event '{intent.calendar_title}' for {intent.start_at.strftime('%Y-%m-%d %H:%M')}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not create the calendar event '{intent.calendar_title}': {exc}"
                status = "blocked"
        elif intent.kind == "file.move" and intent.source_path and intent.destination_path:
            try:
                move_call = self.tool_registry.move_file(intent.source_path, intent.destination_path)
                tool_calls.append(move_call)
                raw_output = (
                    f"Moved {intent.source_path} to {intent.destination_path}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not move {intent.source_path} to {intent.destination_path}: {exc}"
                status = "blocked"
        elif intent.kind in {"document.open", "document.open_recent"} and intent.query:
            try:
                if intent.kind == "document.open_recent":
                    results, search_call = self.tool_registry.search_recent_documents(intent.query, limit=5)
                else:
                    results, search_call = self.tool_registry.search_spotlight_files(intent.query, limit=5)
                tool_calls.append(search_call)
                if not results:
                    raw_output = f"No matching document was found for '{intent.query}'."
                    status = "blocked"
                else:
                    if intent.target_app:
                        open_call = self.tool_registry.execute_capability(
                            "document",
                            "open_path",
                            user_id=context.user_id,
                            requested_app=intent.target_app,
                            path=results[0],
                        )
                    else:
                        open_call = self.tool_registry.open_path(results[0])
                    tool_calls.append(open_call)
                    raw_output = (
                        f"Opened the document {results[0]}. Relevant graph context: "
                        f"{'; '.join(graph_summary[:3])}."
                    )
                    status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not open the document for '{intent.query}': {exc}"
                status = "blocked"
        elif intent.kind == "notes.search" and intent.query:
            try:
                notes_call = self.tool_registry.execute_capability(
                    "notes",
                    "search",
                    user_id=context.user_id,
                    requested_app=intent.target_app or "Notes",
                    query=intent.query,
                )
                tool_calls.append(notes_call)
                raw_output = (
                    f"Searched Notes for '{intent.query}'. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not search Notes for '{intent.query}': {exc}"
                status = "blocked"
        elif intent.kind == "messaging.draft":
            try:
                message_call = self.tool_registry.execute_capability(
                    "messaging",
                    "draft",
                    user_id=context.user_id,
                    requested_app=intent.target_app or "Slack",
                    recipient=intent.recipient,
                    body=intent.body,
                )
                tool_calls.append(message_call)
                raw_output = (
                    f"Prepared a message draft in {intent.target_app or 'the chat app'}"
                    f"{f' for {intent.recipient}' if intent.recipient else ''}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not prepare that message draft: {exc}"
                status = "blocked"
        elif intent.kind == "messaging.open":
            try:
                message_call = self.tool_registry.execute_capability(
                    "messaging",
                    "open",
                    user_id=context.user_id,
                    requested_app=intent.target_app or "Slack",
                )
                tool_calls.append(message_call)
                raw_output = (
                    f"Opened {intent.target_app or 'the chat app'}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not open that chat app: {exc}"
                status = "blocked"
        elif intent.kind == "browser.focus_tab" and intent.query:
            try:
                tab_call = self.tool_registry.focus_browser_tab(intent.query)
                tool_calls.append(tab_call)
                raw_output = (
                    f"Focused the browser tab matching '{intent.query}'. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not focus the browser tab '{intent.query}': {exc}"
                status = "blocked"
        elif intent.kind == "browser.switch_tab" and intent.direction:
            try:
                tab_call = self.tool_registry.switch_browser_tab(intent.direction)
                tool_calls.append(tab_call)
                raw_output = (
                    f"Switched to the {intent.direction} browser tab. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not switch browser tabs: {exc}"
                status = "blocked"
        elif intent.kind == "browser.search" and intent.query:
            try:
                search_call = self.tool_registry.execute_capability(
                    "browser",
                    "search",
                    user_id=context.user_id,
                    requested_app=intent.target_app,
                    query=intent.query,
                )
                tool_calls.append(search_call)
                raw_output = (
                    f"Opened a new browser search for '{intent.query}'"
                    f"{f' in {intent.target_app}' if intent.target_app else ''}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not open a browser search for '{intent.query}': {exc}"
                status = "blocked"
        elif intent.kind == "email.compose":
            try:
                email_call = self.tool_registry.execute_capability(
                    "email",
                    "compose",
                    user_id=context.user_id,
                    requested_app=intent.target_app or "Mail",
                    recipient=intent.recipient,
                    subject=intent.subject,
                    body=intent.body,
                )
                tool_calls.append(email_call)
                raw_output = (
                    f"Opened a Mail draft"
                    f"{f' to {intent.recipient}' if intent.recipient else ''}"
                    f"{f' about {intent.subject}' if intent.subject else ''}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not open a Mail draft: {exc}"
                status = "blocked"
        elif intent.kind == "media.youtube" and intent.media_action:
            try:
                media_call = self.tool_registry.control_youtube_playback(intent.media_action)
                tool_calls.append(media_call)
                raw_output = (
                    f"{intent.media_action.capitalize()}d YouTube playback. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not {intent.media_action} YouTube playback: {exc}"
                status = "blocked"
        elif intent.kind == "media.browser" and intent.media_action:
            try:
                media_call = self.tool_registry.control_browser_media(intent.media_action)
                tool_calls.append(media_call)
                raw_output = (
                    f"{self._past_tense(intent.media_action)} the frontmost browser video. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not {intent.media_action} the frontmost browser video: {exc}"
                status = "blocked"
        elif intent.kind == "media.app" and intent.media_action:
            try:
                media_call = self.tool_registry.execute_capability(
                    "media_app",
                    "control",
                    user_id=context.user_id,
                    requested_app=intent.target_app,
                    action=intent.media_action,
                )
                tool_calls.append(media_call)
                raw_output = (
                    f"{self._past_tense(intent.media_action)} the active media app. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not {intent.media_action} the active media app: {exc}"
                status = "blocked"
        elif intent.kind == "unknown":
            raw_output = "No supported action was identified. No application was opened."
            status = "blocked"
        else:
            app_name = intent.target_app or self._extract_application_name(task.description)
            if not app_name:
                raw_output = "No application name or media action could be confidently extracted from the task."
                status = "blocked"
            else:
                try:
                    if intent.kind == "app.activate" or any(token in lowered for token in ["switch to", "focus ", "bring ", "go to "]):
                        app_call = self.tool_registry.execute_capability(
                            "app",
                            "activate",
                            user_id=context.user_id,
                            requested_app=app_name,
                        )
                    else:
                        app_call = self.tool_registry.execute_capability(
                            "app",
                            "open",
                            user_id=context.user_id,
                            requested_app=app_name,
                        )
                    tool_calls.append(app_call)
                    raw_output = (
                        f"Opened {app_name}. Relevant graph context: {'; '.join(graph_summary[:3])}."
                    )
                    status = "completed"
                except Exception as exc:  # noqa: BLE001
                    raw_output = f"Could not open {app_name}: {exc}"
                    status = "blocked"

        return AgentExecution(
            task_id=task.task_id,
            specialist=task.specialist,
            status=status,
            reasoning="This task looked like a direct macOS application action.",
            tool_calls=tool_calls,
            output=self._specialist_handoff(
                task=task,
                raw_output=raw_output,
                context=context,
                history=history,
                fallback=raw_output,
            ),
        )

    def _run_workspace_task(
        self,
        task: AgentTask,
        *,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> AgentExecution:
        files, files_call = self.tool_registry.list_workspace_files()
        tool_calls = [files_call]
        raw_output = f"Workspace sample files: {', '.join(files[:10])}."

        file_reference = self._extract_file_reference(task.description, files)
        if file_reference:
            content, read_call = self.tool_registry.read_workspace_file(file_reference)
            tool_calls.append(read_call)
            preview = content[:400].replace("\n", " ")
            raw_output = (
                f"Read {file_reference}. Preview: {preview}"
            )

        return AgentExecution(
            task_id=task.task_id,
            specialist=task.specialist,
            status="completed",
            reasoning="This task looked like code or workspace inspection.",
            tool_calls=tool_calls,
            output=self._specialist_handoff(
                task=task,
                raw_output=raw_output,
                context=context,
                history=history,
                fallback=raw_output,
            ),
        )

    def _run_planner_task(
        self,
        task: AgentTask,
        *,
        user_id: str,
        conversation_id: str,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> AgentExecution:
        profile, profile_call = self.tool_registry.get_profile(user_id)
        history, history_call = self.tool_registry.get_conversation_history(user_id, conversation_id)
        raw_output = (
            f"Planning context includes {len(profile.preferences)} stored preferences and "
            f"{len(history)} recent conversation messages."
        )
        return AgentExecution(
            task_id=task.task_id,
            specialist=task.specialist,
            status="completed",
            reasoning="This task asked for decomposition or structured planning.",
            tool_calls=[profile_call, history_call],
            output=self._specialist_handoff(
                task=task,
                raw_output=raw_output,
                context=context,
                history=history,
                fallback=raw_output,
            ),
        )

    def _run_writer_task(
        self,
        task: AgentTask,
        *,
        user_id: str,
        context: AssistantContext,
        history: ConversationHistory,
    ) -> AgentExecution:
        _, profile_call = self.tool_registry.get_profile(user_id)
        raw_output = f"Prepared material for the final Cleo response based on: {task.description}"
        return AgentExecution(
            task_id=task.task_id,
            specialist=task.specialist,
            status="completed",
            reasoning="This task needed synthesis or user-facing phrasing.",
            tool_calls=[profile_call],
            output=self._specialist_handoff(
                task=task,
                raw_output=raw_output,
                context=context,
                history=history,
                fallback=raw_output,
            ),
        )

    def _compose_final_response(
        self,
        *,
        request: CommandRequest,
        context: AssistantContext,
        history: ConversationHistory,
        tasks: list[AgentTask],
        executions: list[AgentExecution],
    ) -> LLMReply:
        execution_text = "\n".join(
            f"- {execution.specialist} completed {execution.task_id}: {execution.output}"
            for execution in executions
        )
        prompt = (
            "You are Cleo's writer specialist. Summarize the command execution for the user. "
            "Explain how the command was broken into subtasks, what each specialist did, and "
            "what happened. Keep it concise but concrete.\n\n"
            f"Original command: {request.command}\n"
            f"Tasks: {', '.join(task.title for task in tasks)}\n"
            f"Executions:\n{execution_text}"
        )
        return self.llm.chat(
            ChatRequest(
                message=prompt,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
            ),
            context,
            history,
        )

    def _extract_file_reference(self, description: str, files: list[str]) -> str | None:
        matches = re.findall(r"[\w./-]+\.(?:py|md|tsx|ts|json|toml|yml|yaml)", description)
        if not matches:
            return None
        for match in matches:
            if match in files:
                return match
        return None

    def _specialist_handoff(
        self,
        *,
        task: AgentTask,
        raw_output: str,
        context: AssistantContext,
        history: ConversationHistory,
        fallback: str,
    ) -> str:
        if task.specialist in {"action", "memory", "connector", "workspace"}:
            return fallback
        prompt = self._build_specialist_prompt(task, raw_output)
        try:
            reply = self.llm.chat(
                ChatRequest(
                    message=prompt,
                    user_id=context.user_id,
                    conversation_id=context.conversation_id,
                ),
                context,
                history,
            )
        except LLMError:
            return fallback
        cleaned = reply.content.strip()
        return cleaned or fallback

    def _build_specialist_prompt(self, task: AgentTask, raw_output: str) -> str:
        role = self._specialist_role(task.specialist)
        return (
            f"You are Cleo's {task.specialist} specialist.\n"
            f"Role: {role}\n"
            "Turn the raw task result into a short internal handoff for the next specialist. "
            "Be concrete, avoid filler, and stay under three sentences.\n\n"
            f"Task: {task.description}\n"
            f"Raw result: {raw_output}"
        )

    def _specialist_role(self, specialist: str) -> str:
        roles = {
            "memory": "Extract or confirm durable user preferences, habits, or workflows.",
            "connector": "Summarize relevant app, graph, or integration context.",
            "workspace": "Inspect files or code and report only the useful findings.",
            "planner": "Break work into the smallest sensible next steps.",
            "action": "Turn a user request into a concrete operating-system action.",
            "writer": "Shape findings into a clean, user-facing summary.",
        }
        return roles.get(specialist, "Handle a narrow subtask clearly and efficiently.")

    @staticmethod
    def _is_non_action_message(description: str) -> bool:
        lowered = description.lower()
        # Questions about performing an action are not authorization to execute it.
        if re.match(r"\s*(?:how\b|why\b|what\b|when\b|where\b|explain\b|tell me about\b)", lowered):
            return True
        if re.search(r"\b(?:do not|don't|dont|never)\s+(?:open|launch|start|play|pause|stop|send|move|run|delete)\b", lowered):
            return True
        return False

    def _parse_action_intent(self, description: str) -> ActionIntent:
        lowered = description.lower()
        if self._is_non_action_message(description):
            return ActionIntent(kind="unknown")
        explicit_app = self._extract_explicit_target_app(description)
        media_action = self._extract_media_action(description)
        codex_prompt = self._extract_codex_prompt(description)
        if codex_prompt:
            return ActionIntent(kind="codex.delegate", target_app=explicit_app or "Terminal", codex_prompt=codex_prompt)

        shortcut_name = self._extract_shortcut_name(description)
        if shortcut_name:
            return ActionIntent(kind="shortcut.run", shortcut_name=shortcut_name)

        calendar_details = self._extract_calendar_event_details(description)
        if calendar_details:
            title, start_at, end_at = calendar_details
            return ActionIntent(
                kind="calendar.create",
                calendar_title=title,
                start_at=start_at,
                end_at=end_at,
            )

        move_paths = self._extract_move_paths(description)
        if move_paths:
            return ActionIntent(kind="file.move", source_path=move_paths[0], destination_path=move_paths[1])

        browser_search_query = self._extract_browser_search_query(description)
        browser_name = self._extract_browser_name(description)
        if browser_search_query:
            return ActionIntent(kind="browser.search", target_app=browser_name or explicit_app, query=browser_search_query)

        browser_tab_query = self._extract_browser_tab_query(description)
        if browser_tab_query:
            return ActionIntent(kind="browser.focus_tab", query=browser_tab_query)

        browser_tab_direction = self._extract_browser_tab_direction(description)
        if browser_tab_direction:
            return ActionIntent(kind="browser.switch_tab", direction=browser_tab_direction)

        note_query = self._extract_note_query(description)
        if note_query and ("note" in lowered or explicit_app == "Notes"):
            return ActionIntent(kind="notes.search", target_app="Notes", query=note_query)

        document_query = self._extract_document_query(description)
        if document_query:
            return ActionIntent(kind="document.open", target_app=explicit_app, query=document_query)
        if self._looks_like_recent_document_request(description):
            return ActionIntent(kind="document.open_recent", target_app=explicit_app, query=self._fallback_document_query(description))

        message_recipient, message_body = self._extract_message_details(description)
        if explicit_app in {"Slack", "WhatsApp"} and (message_recipient or message_body or self._looks_like_message_action(lowered)):
            return ActionIntent(kind="messaging.draft", target_app=explicit_app, recipient=message_recipient, body=message_body)
        if any(token in lowered for token in ["slack", "whatsapp", "message ", "chat ", "reply "]) and (message_recipient or message_body):
            return ActionIntent(kind="messaging.draft", target_app=explicit_app or "Slack", recipient=message_recipient, body=message_body)
        if explicit_app in {"Slack", "WhatsApp"} and self._looks_like_message_action(lowered):
            return ActionIntent(kind="messaging.open", target_app=explicit_app)

        if ("mail" in lowered or "email" in lowered) and self._looks_like_email_action(lowered):
            recipient, subject, body = self._extract_email_details(description)
            return ActionIntent(kind="email.compose", target_app=explicit_app or "Mail", recipient=recipient, subject=subject, body=body)

        if "youtube" in lowered and media_action:
            return ActionIntent(kind="media.youtube", media_action=media_action)
        if media_action and any(
            token in lowered for token in ["video", "browser", "tab", "window", "stream", "player", "netflix", "vimeo", "twitch"]
        ):
            return ActionIntent(kind="media.browser", media_action=media_action)
        if media_action and any(token in lowered for token in ["spotify", "music", "song", "track", "podcast", "audio"]):
            return ActionIntent(kind="media.app", target_app=explicit_app, media_action=media_action)

        app_name = self._extract_application_name(description)
        if not app_name:
            app_name = explicit_app
        if app_name:
            activate = any(token in lowered for token in ["switch to", "focus ", "bring ", "go to "])
            return ActionIntent(kind="app.activate" if activate else "app.open", target_app=app_name)

        return ActionIntent(kind="unknown")

    def _extract_application_name(self, description: str) -> str | None:
        match = re.search(
            r"\b(?:open|launch|start)\s+(?:the\s+)?([A-Za-z0-9][A-Za-z0-9 .&+-]{1,40})",
            description,
            re.IGNORECASE,
        )
        if not match:
            return None
        candidate = match.group(1).strip(" .")
        stop_words = {"app", "application"}
        parts = [part for part in candidate.split() if part.lower() not in stop_words]
        value = " ".join(parts).strip()
        return self._canonical_known_app_name(value) if value else None

    def _extract_explicit_target_app(self, description: str) -> str | None:
        patterns = [
            r"\b(?:switch to|focus|bring up|open|launch|start|go to)\s+(?:the\s+)?(arc|mail|notes|finder|calendar|whatsapp|slack|terminal|music|spotify|safari|chrome|google chrome|brave|edge|microsoft edge|visual studio code|vs code)\b",
            r"\b(?:in|on)\s+(arc|mail|notes|finder|calendar|whatsapp|slack|terminal|music|spotify|safari|chrome|google chrome|brave|edge|microsoft edge|visual studio code|vs code)\b",
        ]
        aliases = {
            "arc": "Arc",
            "mail": "Mail",
            "notes": "Notes",
            "finder": "Finder",
            "calendar": "Calendar",
            "whatsapp": "WhatsApp",
            "slack": "Slack",
            "terminal": "Terminal",
            "music": "Music",
            "spotify": "Spotify",
            "safari": "Safari",
            "chrome": "Google Chrome",
            "google chrome": "Google Chrome",
            "brave": "Brave Browser",
            "edge": "Microsoft Edge",
            "microsoft edge": "Microsoft Edge",
            "visual studio code": "Visual Studio Code",
            "vs code": "Visual Studio Code",
        }
        for pattern in patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return aliases.get(match.group(1).strip().lower(), match.group(1).strip())
        return None

    def _canonical_known_app_name(self, value: str) -> str:
        aliases = {
            "arc": "Arc",
            "mail": "Mail",
            "notes": "Notes",
            "finder": "Finder",
            "calendar": "Calendar",
            "whatsapp": "WhatsApp",
            "slack": "Slack",
            "terminal": "Terminal",
            "music": "Music",
            "spotify": "Spotify",
            "safari": "Safari",
            "chrome": "Google Chrome",
            "google chrome": "Google Chrome",
            "brave": "Brave Browser",
            "edge": "Microsoft Edge",
            "microsoft edge": "Microsoft Edge",
            "visual studio code": "Visual Studio Code",
            "vs code": "Visual Studio Code",
        }
        return aliases.get(value.strip().lower(), value)

    def _extract_media_action(self, description: str) -> str | None:
        lowered = description.lower()
        if re.search(r"\b(?:pause|stop)\b", lowered):
            return "pause"
        if re.search(r"\bresume\b", lowered):
            return "resume"
        if re.search(r"\bnext\b", lowered):
            return "next"
        if re.search(r"\b(?:previous|back)\b", lowered):
            return "previous"
        if re.search(r"\bplay\b", lowered):
            return "play"
        return None

    def _looks_like_email_action(self, lowered: str) -> bool:
        return any(token in lowered for token in ["send", "draft", "write", "compose", "reply"])

    def _extract_email_details(self, description: str) -> tuple[str | None, str | None, str | None]:
        recipient_match = re.search(r"\bto\s+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", description)
        recipient = recipient_match.group(1) if recipient_match else None

        subject_match = re.search(r"\b(?:about|subject)\s+(.+?)(?:\s+\b(?:saying|that says|body)\b|$)", description, re.IGNORECASE)
        subject = subject_match.group(1).strip(" .") if subject_match else None

        body_match = re.search(r"\b(?:saying|that says|body)\s+(.+)$", description, re.IGNORECASE)
        body = body_match.group(1).strip() if body_match else None

        return recipient, subject, body

    def _extract_codex_prompt(self, description: str) -> str | None:
        match = re.search(r"\b(?:tell|ask)\s+codex\s+to\s+(.+)$", description, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if description.lower().startswith("codex "):
            return description[6:].strip()
        return None

    def _extract_shortcut_name(self, description: str) -> str | None:
        quoted = re.search(r'\b(?:run|trigger|launch|start)\s+(?:the\s+)?(?:shortcut\s+)?["“](.+?)["”]', description, re.IGNORECASE)
        if quoted:
            return quoted.group(1).strip()
        bare = re.search(r"\b(?:run|trigger|launch|start)\s+(?:the\s+)?shortcut\s+(.+)$", description, re.IGNORECASE)
        if bare:
            return bare.group(1).strip(" .")
        return None

    def _extract_browser_tab_query(self, description: str) -> str | None:
        quoted = re.search(r'\b(?:switch to|focus|go to|find)\s+(?:the\s+)?["“](.+?)["”]\s+tab', description, re.IGNORECASE)
        if quoted:
            return quoted.group(1).strip()
        bare = re.search(r"\b(?:switch to|focus|go to)\s+(.+?)\s+tab\b", description, re.IGNORECASE)
        if bare:
            query = bare.group(1).strip(" .")
            query = re.sub(r"^the\s+", "", query, flags=re.IGNORECASE)
            if query.lower() not in {"next", "previous"}:
                return query
        return None

    def _extract_browser_tab_direction(self, description: str) -> str | None:
        lowered = description.lower()
        if "next tab" in lowered:
            return "next"
        if "previous tab" in lowered or "prev tab" in lowered:
            return "previous"
        return None

    def _extract_browser_search_query(self, description: str) -> str | None:
        quoted = re.search(
            r'\b(?:search for|look for|google)\s+["“](.+?)["”]',
            description,
            re.IGNORECASE,
        )
        if quoted:
            return quoted.group(1).strip()

        bare = re.search(
            r"\b(?:search for|look for|google)\s+(.+)$",
            description,
            re.IGNORECASE,
        )
        if not bare:
            return None

        query = bare.group(1).strip(" .")
        query = re.sub(r"\b(?:on|in)\s+(?:arc|chrome|google chrome|brave|safari|edge|microsoft edge)\b.*$", "", query, flags=re.IGNORECASE).strip(" .")
        return query or None

    def _extract_browser_name(self, description: str) -> str | None:
        match = re.search(
            r"\b(?:on|in)\s+(arc|chrome|google chrome|brave|safari|edge|microsoft edge)\b",
            description,
            re.IGNORECASE,
        )
        if match:
            aliases = {
                "arc": "Arc",
                "chrome": "Google Chrome",
                "google chrome": "Google Chrome",
                "brave": "Brave Browser",
                "safari": "Safari",
                "edge": "Microsoft Edge",
                "microsoft edge": "Microsoft Edge",
            }
            return aliases.get(match.group(1).strip().lower(), match.group(1).strip())
        return None

    def _extract_note_query(self, description: str) -> str | None:
        quoted = re.search(r'\b(?:find|search|look for|open)\s+(?:in\s+)?notes?\s+["“](.+?)["”]', description, re.IGNORECASE)
        if quoted:
            return quoted.group(1).strip()
        bare = re.search(r"\b(?:find|search|look for)\s+(?:in\s+)?notes?\s+(.+)$", description, re.IGNORECASE)
        if bare:
            return bare.group(1).strip(" .")
        alt = re.search(r"\b(?:find|search|look for)\s+(.+?)\s+in\s+notes?\b", description, re.IGNORECASE)
        if alt:
            return alt.group(1).strip(" .")
        return None

    def _looks_like_message_action(self, lowered: str) -> bool:
        return any(token in lowered for token in ["message", "chat", "reply", "send", "draft", "write"])

    def _extract_message_details(self, description: str) -> tuple[str | None, str | None]:
        recipient = None
        body = None
        recipient_match = re.search(
            r"\b(?:to|with)\s+([A-Za-z0-9][A-Za-z0-9 ._-]{1,60}?)(?:\s+\b(?:saying|that says|message|about)\b|$)",
            description,
            re.IGNORECASE,
        )
        if recipient_match:
            recipient = recipient_match.group(1).strip(" .")
        body_match = re.search(r"\b(?:saying|that says)\s+(.+)$", description, re.IGNORECASE)
        if not body_match:
            body_match = re.search(r"\bmessage\s+(.+)$", description, re.IGNORECASE)
        if body_match:
            body = body_match.group(1).strip()
        return recipient, body

    def _extract_move_paths(self, description: str) -> tuple[str, str] | None:
        quoted = re.search(r'\b(?:move|rename)\s+["“](.+?)["”]\s+to\s+["“](.+?)["”]', description, re.IGNORECASE)
        if quoted:
            return quoted.group(1).strip(), quoted.group(2).strip()
        bare = re.search(r"\b(?:move|rename)\s+(\S+)\s+to\s+(\S+)", description, re.IGNORECASE)
        if bare:
            return bare.group(1).strip(), bare.group(2).strip()
        return None

    def _extract_document_query(self, description: str) -> str | None:
        quoted = re.search(r'\b(?:open|find|show)\s+(?:the\s+)?(?:document|doc|file|pdf|note)\s+["“](.+?)["”]', description, re.IGNORECASE)
        if quoted:
            return quoted.group(1).strip()
        bare = re.search(r"\b(?:open|find|show)\s+(?:the\s+)?(?:document|doc|file|pdf|note)\s+(.+)$", description, re.IGNORECASE)
        if bare:
            return bare.group(1).strip(" .")
        return None

    def _looks_like_recent_document_request(self, description: str) -> bool:
        lowered = description.lower()
        return any(token in lowered for token in ["recent document", "recent file", "last file", "latest file", "document from yesterday"])

    def _fallback_document_query(self, description: str) -> str:
        lowered = description.lower()
        cleaned = re.sub(r"\b(?:open|find|show|recent|latest|last|document|doc|file|pdf|note|from|yesterday|today|my|about)\b", " ", lowered)
        cleaned = re.sub(
            r"\b(?:in|on)\s+(?:arc|mail|notes|finder|calendar|whatsapp|slack|terminal|music|spotify|safari|chrome|google chrome|brave|edge|microsoft edge|visual studio code|vs code)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned or description.strip()

    def _extract_calendar_event_details(self, description: str) -> tuple[str, datetime, datetime] | None:
        lowered = description.lower()
        if not any(token in lowered for token in ["calendar", "event", "meeting", "appointment"]):
            return None

        start_at = self._extract_start_datetime(description)
        if not start_at:
            return None

        title = self._extract_event_title(description) or "Cleo event"
        end_at = self._extract_end_datetime(description, start_at) or (start_at + timedelta(hours=1))
        return title, start_at, end_at

    def _extract_event_title(self, description: str) -> str | None:
        for pattern in [
            r'\b(?:called|titled)\s+["“]?(.+?)["”]?(?:\s+(?:today|tomorrow|on|at|from)\b|$)',
            r"\b(?:schedule|create|add)\s+(?:a\s+)?(?:calendar\s+)?(?:event|meeting|appointment)\s+(.+?)(?:\s+(?:today|tomorrow|on|at|from)\b|$)",
        ]:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return match.group(1).strip(" .")
        return None

    def _extract_start_datetime(self, description: str) -> datetime | None:
        now = datetime.now().astimezone()
        day_offset = 0
        if "tomorrow" in description.lower():
            day_offset = 1

        explicit = re.search(r"\bon\s+(\d{4}-\d{2}-\d{2})\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", description, re.IGNORECASE)
        if explicit:
            date_part = explicit.group(1)
            hour = int(explicit.group(2))
            minute = int(explicit.group(3) or "0")
            meridiem = explicit.group(4)
            hour = self._normalize_hour(hour, meridiem)
            return datetime.fromisoformat(f"{date_part}T00:00:00").replace(
                hour=hour,
                minute=minute,
                tzinfo=now.tzinfo,
            )

        relative = re.search(r"\b(?:today|tomorrow)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", description, re.IGNORECASE)
        if relative:
            hour = int(relative.group(1))
            minute = int(relative.group(2) or "0")
            meridiem = relative.group(3)
            hour = self._normalize_hour(hour, meridiem)
            base = (now + timedelta(days=day_offset)).replace(second=0, microsecond=0)
            return base.replace(hour=hour, minute=minute)

        at_time = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", description, re.IGNORECASE)
        if at_time:
            hour = int(at_time.group(1))
            minute = int(at_time.group(2) or "0")
            meridiem = at_time.group(3)
            hour = self._normalize_hour(hour, meridiem)
            base = (now + timedelta(days=day_offset)).replace(second=0, microsecond=0)
            return base.replace(hour=hour, minute=minute)

        return None

    def _extract_end_datetime(self, description: str, start_at: datetime) -> datetime | None:
        end_match = re.search(r"\b(?:until|to)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", description, re.IGNORECASE)
        if not end_match:
            return None
        hour = int(end_match.group(1))
        minute = int(end_match.group(2) or "0")
        meridiem = end_match.group(3)
        hour = self._normalize_hour(hour, meridiem)
        end_at = start_at.replace(hour=hour, minute=minute)
        if end_at <= start_at:
            end_at = end_at + timedelta(days=1)
        return end_at

    def _normalize_hour(self, hour: int, meridiem: str | None) -> int:
        if meridiem is None:
            return hour
        normalized = meridiem.lower()
        if normalized == "pm" and hour != 12:
            return hour + 12
        if normalized == "am" and hour == 12:
            return 0
        return hour

    def _past_tense(self, action: str) -> str:
        mapping = {
            "pause": "Paused",
            "play": "Started",
            "resume": "Resumed",
            "toggle": "Toggled",
            "next": "Skipped to the next item in",
            "previous": "Went to the previous item in",
        }
        return mapping.get(action, action.capitalize())

    def _direct_success_line(self, task: AgentTask, execution: AgentExecution) -> str:
        intent = self._parse_action_intent(task.description)
        if intent.kind == "browser.search" and intent.query:
            if intent.target_app:
                return f"Opened a new {intent.target_app} search for '{intent.query}'."
            return f"Opened a browser search for '{intent.query}'."
        if intent.kind == "notes.search" and intent.query:
            return f"Searched Notes for '{intent.query}'."
        if intent.kind == "messaging.draft":
            return "Prepared a message draft."
        if intent.kind == "messaging.open":
            return f"Opened {intent.target_app or 'the chat app'}."
        if intent.kind in {"app.open", "app.activate"} and intent.target_app:
            return f"Opened {intent.target_app}."
        if intent.kind == "shortcut.run" and intent.shortcut_name:
            return f"Ran the Shortcut {intent.shortcut_name}."
        if intent.kind == "calendar.create" and intent.calendar_title:
            return f"Created the calendar event '{intent.calendar_title}'."
        if intent.kind == "file.move" and intent.source_path and intent.destination_path:
            return f"Moved {intent.source_path} to {intent.destination_path}."
        if intent.kind in {"document.open", "document.open_recent"} and intent.query:
            return f"Opened the best matching document for '{intent.query}'."
        if intent.kind == "email.compose":
            return "Opened a Mail draft."
        if intent.kind == "codex.delegate":
            return "Handed the task to Codex."
        if intent.kind == "browser.focus_tab" and intent.query:
            return f"Focused the browser tab matching '{intent.query}'."
        if intent.kind == "browser.switch_tab" and intent.direction:
            return f"Switched to the {intent.direction} browser tab."
        if intent.kind == "media.youtube" and intent.media_action:
            return f"{self._past_tense(intent.media_action)} YouTube playback."
        if intent.kind == "media.browser" and intent.media_action:
            return f"{self._past_tense(intent.media_action)} the frontmost browser video."
        if intent.kind == "media.app" and intent.media_action:
            return f"{self._past_tense(intent.media_action)} the active media app."
        return execution.output.strip()

    def _direct_blocked_line(self, task: AgentTask, execution: AgentExecution) -> str:
        intent = self._parse_action_intent(task.description)
        if intent.kind == "browser.search" and intent.query:
            return f"Couldn't open a browser search for '{intent.query}'."
        if intent.kind == "notes.search":
            return "Couldn't search Notes."
        if intent.kind == "messaging.draft":
            return "Couldn't prepare that message draft."
        if intent.kind == "messaging.open":
            return "Couldn't open that chat app."
        if intent.kind in {"app.open", "app.activate"} and intent.target_app:
            return f"Couldn't open {intent.target_app}."
        if intent.kind == "shortcut.run":
            return "Couldn't run that Shortcut."
        if intent.kind == "calendar.create":
            return "Couldn't create that calendar event."
        if intent.kind == "file.move":
            return "Couldn't move that file."
        if intent.kind in {"document.open", "document.open_recent"}:
            return "Couldn't open that document."
        if intent.kind in {"browser.focus_tab", "browser.switch_tab"}:
            return "Couldn't switch to that browser tab."
        if intent.kind == "codex.delegate":
            return "Couldn't hand that task to Codex."
        if intent.kind == "email.compose":
            return "Couldn't open the Mail draft."
        return execution.output.strip()
