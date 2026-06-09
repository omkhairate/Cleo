from dataclasses import dataclass
import re

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
        normalized = command.replace(" then ", " and ")
        pieces = [
            piece.strip(" ,.")
            for piece in normalized.split(" and ")
            if piece.strip(" ,.")
        ]
        return pieces or [command.strip()]

    def _pick_specialist(self, chunk: str) -> str:
        lowered = chunk.lower()
        if any(token in lowered for token in ["remember", "prefer", "call me", "profile", "preference"]):
            return "memory"
        if any(token in lowered for token in ["open ", "launch ", "start ", "open app", "application"]):
            return "action"
        if any(
            token in lowered
            for token in [
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
                "codex",
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
                "control_youtube_playback",
                "control_browser_media",
                "control_media_app",
                "compose_email_draft",
                "delegate_to_codex",
                "get_relevant_graph_summary",
            ]
        return ["get_profile"]

    def _title_for_chunk(self, chunk: str) -> str:
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
        media_action = self._extract_media_action(task.description)
        lowered = task.description.lower()
        codex_prompt = self._extract_codex_prompt(task.description)
        if codex_prompt:
            try:
                codex_call = self.tool_registry.delegate_to_codex(codex_prompt)
                tool_calls.append(codex_call)
                raw_output = (
                    f"Delegated to Codex with prompt: {codex_prompt[:160]}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not delegate to Codex: {exc}"
                status = "blocked"
        elif ("mail" in lowered or "email" in lowered) and self._looks_like_email_action(lowered):
            recipient, subject, body = self._extract_email_details(task.description)
            try:
                email_call = self.tool_registry.compose_email_draft(recipient, subject, body)
                tool_calls.append(email_call)
                raw_output = (
                    f"Opened a Mail draft"
                    f"{f' to {recipient}' if recipient else ''}"
                    f"{f' about {subject}' if subject else ''}. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not open a Mail draft: {exc}"
                status = "blocked"
        elif "youtube" in lowered and media_action:
            try:
                media_call = self.tool_registry.control_youtube_playback(media_action)
                tool_calls.append(media_call)
                raw_output = (
                    f"{media_action.capitalize()}d YouTube playback. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not {media_action} YouTube playback: {exc}"
                status = "blocked"
        elif media_action and any(
            token in lowered for token in ["video", "browser", "tab", "window", "stream", "player", "netflix", "vimeo", "twitch"]
        ):
            try:
                media_call = self.tool_registry.control_browser_media(media_action)
                tool_calls.append(media_call)
                raw_output = (
                    f"{self._past_tense(media_action)} the frontmost browser video. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not {media_action} the frontmost browser video: {exc}"
                status = "blocked"
        elif media_action and any(token in lowered for token in ["spotify", "music", "song", "track", "podcast", "audio"]):
            try:
                media_call = self.tool_registry.control_media_app(media_action)
                tool_calls.append(media_call)
                raw_output = (
                    f"{self._past_tense(media_action)} the active media app. Relevant graph context: "
                    f"{'; '.join(graph_summary[:3])}."
                )
                status = "completed"
            except Exception as exc:  # noqa: BLE001
                raw_output = f"Could not {media_action} the active media app: {exc}"
                status = "blocked"
        else:
            app_name = self._extract_application_name(task.description)
            if not app_name:
                raw_output = "No application name or media action could be confidently extracted from the task."
                status = "blocked"
            else:
                try:
                    open_call = self.tool_registry.open_application(app_name)
                    tool_calls.append(open_call)
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
        return " ".join(parts).strip() or None

    def _extract_media_action(self, description: str) -> str | None:
        lowered = description.lower()
        if "pause" in lowered or "stop" in lowered:
            return "pause"
        if "resume" in lowered:
            return "resume"
        if "next" in lowered:
            return "next"
        if "previous" in lowered or "back" in lowered:
            return "previous"
        if "play" in lowered:
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
        description = task.description.lower()
        if any(token in description for token in ["open ", "launch ", "start "]):
            app_name = self._extract_application_name(task.description)
            if app_name:
                return f"Opened {app_name}."
        if any(token in description for token in ["mail", "email"]):
            return "Opened a Mail draft."
        if "codex" in description:
            return "Handed the task to Codex."
        if "youtube" in description:
            action = self._extract_media_action(task.description)
            if action:
                return f"{self._past_tense(action)} YouTube playback."
        if any(token in description for token in ["video", "browser", "tab", "stream", "player"]):
            action = self._extract_media_action(task.description)
            if action:
                return f"{self._past_tense(action)} the frontmost browser video."
        if any(token in description for token in ["spotify", "music", "song", "track", "podcast", "audio"]):
            action = self._extract_media_action(task.description)
            if action:
                return f"{self._past_tense(action)} the active media app."
        return execution.output.strip()

    def _direct_blocked_line(self, task: AgentTask, execution: AgentExecution) -> str:
        app_name = self._extract_application_name(task.description)
        if app_name:
            return f"Couldn't open {app_name}."
        if "codex" in task.description.lower():
            return "Couldn't hand that task to Codex."
        if any(token in task.description.lower() for token in ["mail", "email"]):
            return "Couldn't open the Mail draft."
        return execution.output.strip()
