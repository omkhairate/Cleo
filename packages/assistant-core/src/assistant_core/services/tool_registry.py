from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
import json
import re
import shutil
import subprocess
from urllib.parse import quote_plus

from assistant_core.config import Settings
from assistant_core.models import ToolCallRecord, UserProfile
from assistant_core.services.memory import InMemoryBrainGraphStore, InMemoryConversationStore, InMemoryProfileStore


@dataclass(frozen=True)
class CapabilityAdapter:
    app_name: str
    capability_family: str
    operations: tuple[str, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityVerification:
    success: bool
    detail: str


class CapabilityVerificationError(RuntimeError):
    """An action ran but could not be verified; do not repeat its side effects."""


class CommandToolRegistry:
    """Small set of deterministic tools specialists can call."""

    @staticmethod
    def _run_process(*args, **kwargs):
        kwargs.setdefault("timeout", 30)
        try:
            return subprocess.run(*args, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("The macOS tool timed out. Check for an app or permission dialog before retrying.") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(f"The macOS tool failed: {detail}") from exc

    def __init__(
        self,
        settings: Settings,
        profile_store: InMemoryProfileStore,
        brain_graph_store: InMemoryBrainGraphStore,
        conversation_store: InMemoryConversationStore,
    ) -> None:
        self.settings = settings
        self.profile_store = profile_store
        self.brain_graph_store = brain_graph_store
        self.conversation_store = conversation_store
        self.workspace_root = Path.cwd()
        self._installed_apps_cache: list[str] | None = None
        self.capability_adapters = [
            CapabilityAdapter("Arc", "browser", ("search", "focus_tab", "switch_tab"), ("arc",)),
            CapabilityAdapter("Google Chrome", "browser", ("search", "focus_tab", "switch_tab"), ("chrome", "google chrome")),
            CapabilityAdapter("Safari", "browser", ("search", "focus_tab", "switch_tab"), ("safari",)),
            CapabilityAdapter("Brave Browser", "browser", ("search", "focus_tab", "switch_tab"), ("brave",)),
            CapabilityAdapter("Microsoft Edge", "browser", ("search", "focus_tab", "switch_tab"), ("edge", "microsoft edge")),
            CapabilityAdapter("Mail", "email", ("compose",), ("mail",)),
            CapabilityAdapter("Calendar", "calendar", ("create",), ("calendar",)),
            CapabilityAdapter("Notes", "notes", ("open", "activate", "search"), ("notes",)),
            CapabilityAdapter("Finder", "document", ("open_path", "activate"), ("finder",)),
            CapabilityAdapter("Visual Studio Code", "document", ("open_path", "activate"), ("visual studio code", "vs code", "code")),
            CapabilityAdapter("Slack", "messaging", ("open", "activate", "draft"), ("slack",)),
            CapabilityAdapter("WhatsApp", "messaging", ("open", "activate", "draft"), ("whatsapp",)),
            CapabilityAdapter("Terminal", "codex", ("delegate", "open", "activate"), ("terminal",)),
            CapabilityAdapter("Spotify", "media_app", ("control",), ("spotify",)),
            CapabilityAdapter("Music", "media_app", ("control",), ("music",)),
        ]

    def get_profile(self, user_id: str) -> tuple[UserProfile, ToolCallRecord]:
        profile = self.profile_store.get_profile(user_id)
        return profile, ToolCallRecord(
            tool_name="get_profile",
            result_summary=(
                f"Loaded profile with {len(profile.preferences)} preferences and "
                f"{len(profile.workflows)} workflows."
            ),
        )

    def set_preference(
        self,
        user_id: str,
        key: str,
        value: str,
        *,
        source: str = "manual",
    ) -> tuple[UserProfile, ToolCallRecord]:
        profile = self.profile_store.set_preference(user_id, key, value, source=source)
        return profile, ToolCallRecord(
            tool_name="set_preference",
            arguments={"key": key, "value": value},
            result_summary=f"Stored preference {key}={value}.",
        )

    def list_connectors(self) -> tuple[list[str], ToolCallRecord]:
        graph = self.brain_graph_store.get_graph()
        connectors = [node.label for node in graph.nodes if node.kind == "connector"]
        return connectors, ToolCallRecord(
            tool_name="list_connectors",
            result_summary=f"Found {len(connectors)} connector domains.",
        )

    def get_graph_summary(self) -> tuple[list[str], ToolCallRecord]:
        graph = self.brain_graph_store.get_graph()
        summary = [
            f"{edge.source} {edge.relation} {edge.target}"
            for edge in graph.edges[:10]
        ]
        return summary, ToolCallRecord(
            tool_name="get_graph_summary",
            result_summary=f"Collected {len(summary)} graph relationships.",
        )

    def get_relevant_graph_summary(self, query: str) -> tuple[list[str], ToolCallRecord]:
        summary = self.brain_graph_store.relevant_summary(query)
        return summary, ToolCallRecord(
            tool_name="get_relevant_graph_summary",
            arguments={"query": query[:120]},
            result_summary=f"Collected {len(summary)} graph relationships relevant to the task.",
        )

    def search_spotlight_applications(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> tuple[list[str], ToolCallRecord]:
        results = self._spotlight_paths(
            f'kMDItemContentType == "com.apple.application-bundle"c && '
            f'(kMDItemDisplayName == "*{self._mdfind_escape(query)}*"cd || '
            f'kMDItemFSName == "*{self._mdfind_escape(query)}*"cd)',
            limit=limit,
            home_only=False,
        )
        apps = [Path(path).stem for path in results]
        return apps, ToolCallRecord(
            tool_name="search_spotlight_applications",
            arguments={"query": query, "limit": str(limit)},
            result_summary=f"Found {len(apps)} Spotlight app matches.",
        )

    def search_spotlight_files(
        self,
        query: str,
        *,
        limit: int = 8,
    ) -> tuple[list[str], ToolCallRecord]:
        escaped = self._mdfind_escape(query)
        results = self._spotlight_paths(
            f'(kMDItemDisplayName == "*{escaped}*"cd || '
            f'kMDItemTextContent == "*{escaped}*"cd || '
            f'kMDItemFSName == "*{escaped}*"cd)',
            limit=max(limit * 5, 20),
        )
        results = self._rank_spotlight_file_paths(query, results, limit=limit, prefer_recent=False)
        return results, ToolCallRecord(
            tool_name="search_spotlight_files",
            arguments={"query": query, "limit": str(limit)},
            result_summary=f"Found {len(results)} Spotlight file matches.",
        )

    def search_recent_documents(
        self,
        query: str | None = None,
        *,
        limit: int = 8,
        days: int = 14,
    ) -> tuple[list[str], ToolCallRecord]:
        escaped_query = self._mdfind_escape(query or "")
        filters = [f'kMDItemLastUsedDate >= $time.now(-{days}d)']
        if escaped_query:
            filters.append(
                f'(kMDItemDisplayName == "*{escaped_query}*"cd || '
                f'kMDItemTextContent == "*{escaped_query}*"cd || '
                f'kMDItemFSName == "*{escaped_query}*"cd)'
            )
        filters.append('kMDItemContentType != "com.apple.application-bundle"c')
        results = self._spotlight_paths(" && ".join(filters), limit=max(limit * 5, 20))
        results = self._rank_spotlight_file_paths(query or "", results, limit=limit, prefer_recent=True)
        return results, ToolCallRecord(
            tool_name="search_recent_documents",
            arguments={"query": query or "", "limit": str(limit), "days": str(days)},
            result_summary=f"Found {len(results)} recent Spotlight document matches.",
        )

    def list_workspace_files(self, limit: int = 30) -> tuple[list[str], ToolCallRecord]:
        files = [
            str(path.relative_to(self.workspace_root))
            for path in sorted(self.workspace_root.rglob("*"))
            if path.is_file() and ".git" not in path.parts
        ][:limit]
        return files, ToolCallRecord(
            tool_name="list_workspace_files",
            arguments={"limit": str(limit)},
            result_summary=f"Listed {len(files)} workspace files.",
        )

    def read_workspace_file(self, relative_path: str, limit: int = 4000) -> tuple[str, ToolCallRecord]:
        path = (self.workspace_root / relative_path).resolve()
        if self.workspace_root not in path.parents and path != self.workspace_root:
            raise ValueError("Requested path is outside the workspace root.")
        content = path.read_text()[:limit]
        return content, ToolCallRecord(
            tool_name="read_workspace_file",
            arguments={"path": relative_path, "limit": str(limit)},
            result_summary=f"Read {relative_path}.",
        )

    def get_conversation_history(
        self,
        user_id: str,
        conversation_id: str,
    ) -> tuple[list[str], ToolCallRecord]:
        history = self.conversation_store.get_history(user_id, conversation_id)
        items = [f"{message.role}: {message.content}" for message in history.messages]
        return items, ToolCallRecord(
            tool_name="get_conversation_history",
            arguments={"conversation_id": conversation_id},
            result_summary=f"Loaded {len(items)} conversation messages.",
        )

    def open_application(self, app_name: str) -> ToolCallRecord:
        resolved_app_name = self._resolve_application_name(app_name)
        if not resolved_app_name:
            raise RuntimeError(f"No exact installed application named '{app_name}' was found. No other app was opened.")
        bundle_path = next((
            root / f"{resolved_app_name}.app"
            for root in (Path("/System/Applications"), Path("/Applications"), Path.home() / "Applications")
            if (root / f"{resolved_app_name}.app").is_dir()
        ), None)
        self._run_process(
            ["/usr/bin/open", str(bundle_path)] if bundle_path else ["/usr/bin/open", "-a", resolved_app_name],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="open_application",
            arguments={"app_name": app_name, "resolved_app_name": resolved_app_name, "bundle_path": str(bundle_path) if bundle_path else "Launch Services exact name"},
            result_summary=f"Opened macOS application '{resolved_app_name}'.",
        )

    def execute_capability(
        self,
        capability_family: str,
        operation: str,
        *,
        user_id: str,
        requested_app: str | None = None,
        **kwargs,
    ) -> ToolCallRecord:
        candidates = self._resolve_capability_candidates(
            capability_family,
            operation,
            user_id=user_id,
            requested_app=requested_app,
        )
        errors: list[str] = []
        attempted: list[str] = []
        for candidate in candidates:
            attempted.append(candidate)
            try:
                record = self._execute_capability_on_app(
                    capability_family,
                    operation,
                    candidate,
                    **kwargs,
                )
                verification = self._verify_capability_execution(
                    capability_family,
                    operation,
                    candidate,
                    **kwargs,
                )
                record.arguments.setdefault("resolved_app_name", candidate)
                record.arguments["capability_family"] = capability_family
                record.arguments["verification_success"] = "true" if verification.success else "false"
                record.arguments["verification_detail"] = verification.detail
                if len(attempted) > 1:
                    record.arguments["fallback_chain"] = " > ".join(attempted)
                if verification.detail:
                    record.result_summary = f"{record.result_summary} Verification: {verification.detail}"
                if not verification.success:
                    raise CapabilityVerificationError(
                        f"The action was sent to {candidate}, but verification failed: {verification.detail}. "
                        "Check the app before retrying; Cleo will not repeat it automatically."
                    )
                return record
            except CapabilityVerificationError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{candidate}: {exc}")

        if requested_app and requested_app not in attempted:
            attempted.insert(0, requested_app)
        raise RuntimeError(
            f"No app could fulfill {capability_family}.{operation}. "
            f"Tried: {', '.join(attempted) or 'none'}. "
            f"Errors: {' | '.join(errors) or 'none'}"
        )

    def open_path(self, raw_path: str) -> ToolCallRecord:
        resolved_path = self._resolve_user_path(raw_path)
        self._run_process(
            ["open", str(resolved_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="open_path",
            arguments={"path": str(resolved_path)},
            result_summary=f"Opened '{resolved_path.name}'.",
        )

    def open_path_in_application(self, raw_path: str, app_name: str) -> ToolCallRecord:
        resolved_path = self._resolve_user_path(raw_path)
        resolved_app_name = self._resolve_application_name(app_name) or app_name
        self._run_process(
            ["open", "-a", resolved_app_name, str(resolved_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="open_path_in_application",
            arguments={"path": str(resolved_path), "app_name": app_name, "resolved_app_name": resolved_app_name},
            result_summary=f"Opened '{resolved_path.name}' in '{resolved_app_name}'.",
        )

    def activate_application(self, app_name: str) -> ToolCallRecord:
        resolved_app_name = self._resolve_application_name(app_name) or app_name
        script = f'''
tell application {json.dumps(resolved_app_name)}
    activate
end tell
'''
        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="activate_application",
            arguments={"app_name": app_name, "resolved_app_name": resolved_app_name},
            result_summary=f"Activated macOS application '{resolved_app_name}'.",
        )

    def control_youtube_playback(self, action: str) -> ToolCallRecord:
        normalized = action.lower().strip()
        if normalized not in {"pause", "play", "resume", "toggle"}:
            raise ValueError(f"Unsupported YouTube playback action: {action}")

        script = self._browser_javascript_script(self._youtube_javascript(normalized))

        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="control_youtube_playback",
            arguments={"action": normalized},
            result_summary=f"Sent a {normalized} command to YouTube or the frontmost media page.",
        )

    def control_browser_media(self, action: str) -> ToolCallRecord:
        normalized = action.lower().strip()
        if normalized not in {"pause", "play", "resume", "toggle"}:
            raise ValueError(f"Unsupported browser media action: {action}")

        script = self._browser_javascript_script(self._browser_media_javascript(normalized))
        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="control_browser_media",
            arguments={"action": normalized},
            result_summary=f"Sent a {normalized} command to the frontmost browser video.",
        )

    def control_media_app(self, action: str, app_name: str | None = None) -> ToolCallRecord:
        normalized = action.lower().strip()
        if normalized not in {"pause", "play", "resume", "toggle", "next", "previous"}:
            raise ValueError(f"Unsupported media action: {action}")

        media_command = {
            "pause": "pause",
            "play": "play",
            "resume": "play",
            "toggle": "playpause",
            "next": "next track",
            "previous": "previous track",
        }[normalized]
        target = app_name or self._frontmost_application_name()
        if target not in {"Music", "Spotify"}:
            target = next((app for app in self._running_application_names() if app in {"Music", "Spotify"}), None)
        if target not in {"Music", "Spotify"}:
            raise RuntimeError("No supported media app is currently running.")

        script = f"""
tell application {json.dumps(target)}
    if it is not running then error "The requested media app is not running."
    {media_command}
end tell
"""

        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="control_media_app",
            arguments={"action": normalized, "app_name": target},
            result_summary=f"Sent a {normalized} command to {target}.",
        )

    def compose_email_draft(
        self,
        recipient: str | None,
        subject: str | None,
        body: str | None,
    ) -> ToolCallRecord:
        recipient_value = recipient or ""
        subject_value = subject or ""
        body_value = body or ""
        content_value = body_value + "\n\n"
        script = f"""
tell application "Mail"
    activate
    set newMessage to make new outgoing message with properties {{subject:{json.dumps(subject_value)}, content:{json.dumps(content_value)}, visible:true}}
    tell newMessage
        if {json.dumps(bool(recipient_value))} then
            make new to recipient at end of to recipients with properties {{address:{json.dumps(recipient_value)}}}
        end if
    end tell
end tell
"""

        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="compose_email_draft",
            arguments={
                "recipient": recipient_value,
                "subject": subject_value,
                "body": body_value[:200],
            },
            result_summary="Opened a draft email in Mail.",
        )

    def search_notes(self, query: str) -> ToolCallRecord:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Notes search query cannot be empty.")
        script = f"""
tell application "Notes"
    activate
end tell
delay 0.2
tell application "System Events"
    keystroke "f" using {{command down}}
    delay 0.1
    keystroke {json.dumps(normalized_query)}
end tell
"""
        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="search_notes",
            arguments={"query": normalized_query},
            result_summary=f"Searched Notes for '{normalized_query}'.",
        )

    def draft_message(
        self,
        app_name: str,
        recipient: str | None,
        body: str | None,
    ) -> ToolCallRecord:
        resolved_app_name = self._resolve_application_name(app_name) or app_name
        recipient_value = (recipient or "").strip()
        body_value = (body or "").strip()
        quoted_body = json.dumps(body_value)
        if self._normalize_app_name(resolved_app_name) == self._normalize_app_name("Slack"):
            script = f"""
set messageBody to {quoted_body}
tell application "Slack"
    activate
end tell
delay 0.2
tell application "System Events"
    if {json.dumps(bool(recipient_value))} then
        keystroke "k" using {{command down}}
        delay 0.15
        keystroke {json.dumps(recipient_value)}
        delay 0.15
        key code 36
        delay 0.2
    end if
    if messageBody is not "" then
        set the clipboard to messageBody
    end if
end tell
"""
        elif self._normalize_app_name(resolved_app_name) == self._normalize_app_name("WhatsApp"):
            script = f"""
set messageBody to {quoted_body}
tell application "WhatsApp"
    activate
end tell
delay 0.2
tell application "System Events"
    if {json.dumps(bool(recipient_value))} then
        keystroke "f" using {{command down}}
        delay 0.15
        keystroke {json.dumps(recipient_value)}
        delay 0.15
        key code 36
        delay 0.2
    end if
    if messageBody is not "" then
        set the clipboard to messageBody
    end if
end tell
"""
        else:
            raise RuntimeError(f"{resolved_app_name} does not support message drafting yet.")

        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        summary = f"Prepared a message draft in {resolved_app_name}."
        if body_value:
            summary += " The message text was copied to the clipboard."
        return ToolCallRecord(
            tool_name="draft_message",
            arguments={
                "app_name": app_name,
                "resolved_app_name": resolved_app_name,
                "recipient": recipient_value,
                "body": body_value[:200],
            },
            result_summary=summary,
        )

    def run_shortcut(self, shortcut_name: str) -> ToolCallRecord:
        self._run_process(
            ["shortcuts", "run", shortcut_name],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="run_shortcut",
            arguments={"shortcut_name": shortcut_name},
            result_summary=f"Ran the Shortcut '{shortcut_name}'.",
        )

    def create_calendar_event(
        self,
        title: str,
        start_at: datetime,
        end_at: datetime,
        notes: str | None = None,
        calendar_name: str | None = None,
    ) -> ToolCallRecord:
        start_values = self._datetime_components(start_at)
        end_values = self._datetime_components(end_at)
        target_calendar = calendar_name or ""
        note_value = notes or ""
        script = f"""
set startDate to current date
set year of startDate to {start_values["year"]}
set month of startDate to {start_values["month"]}
set day of startDate to {start_values["day"]}
set time of startDate to {start_values["seconds"]}

set endDate to current date
set year of endDate to {end_values["year"]}
set month of endDate to {end_values["month"]}
set day of endDate to {end_values["day"]}
set time of endDate to {end_values["seconds"]}

tell application "Calendar"
    activate
    if {json.dumps(bool(target_calendar))} then
        if not (exists calendar {json.dumps(target_calendar)}) then
            error "Calendar '{target_calendar}' does not exist."
        end if
        set targetCalendar to calendar {json.dumps(target_calendar)}
    else
        if (count of calendars) is 0 then
            error "No calendars are available."
        end if
        set targetCalendar to first calendar
    end if
    tell targetCalendar
        make new event with properties {{summary:{json.dumps(title)}, start date:startDate, end date:endDate, description:{json.dumps(note_value)}}}
    end tell
end tell
"""
        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="create_calendar_event",
            arguments={
                "title": title,
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "calendar_name": target_calendar or "default",
            },
            result_summary=f"Created calendar event '{title}'.",
        )

    def move_file(self, source_path: str, destination_path: str) -> ToolCallRecord:
        source = self._resolve_user_path(source_path)
        destination = self._resolve_user_path(destination_path, allow_missing=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return ToolCallRecord(
            tool_name="move_file",
            arguments={"source_path": str(source), "destination_path": str(destination)},
            result_summary=f"Moved '{source.name}' to '{destination}'.",
        )

    def switch_browser_tab(self, direction: str) -> ToolCallRecord:
        normalized = direction.lower().strip()
        if normalized not in {"next", "previous"}:
            raise ValueError(f"Unsupported tab direction: {direction}")

        keystroke = "]" if normalized == "next" else "["
        script = f"""
tell application "System Events"
    keystroke {json.dumps(keystroke)} using {{command down, shift down}}
end tell
"""
        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="switch_browser_tab",
            arguments={"direction": normalized},
            result_summary=f"Switched to the {normalized} browser tab.",
        )

    def focus_browser_tab(self, query: str) -> ToolCallRecord:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Browser tab query cannot be empty.")

        script = f"""
set queryText to {json.dumps(normalized_query)}
set handled to false

try
    tell application "Google Chrome"
        if it is running then
            repeat with w from 1 to count of windows
                set tabIndex to 0
                repeat with t in tabs of window w
                    set tabIndex to tabIndex + 1
                    set titleText to (title of t as text)
                    set urlText to (URL of t as text)
                    ignoring case
                        if (titleText contains queryText) or (urlText contains queryText) then
                            set active tab index of window w to tabIndex
                            set index of window w to 1
                            activate
                            set handled to true
                            exit repeat
                        end if
                    end ignoring
                end repeat
                if handled then exit repeat
            end repeat
        end if
    end tell
end try

if handled is false then
    try
        tell application "Brave Browser"
            if it is running then
                repeat with w from 1 to count of windows
                    set tabIndex to 0
                repeat with t in tabs of window w
                    set tabIndex to tabIndex + 1
                    set titleText to (title of t as text)
                    set urlText to (URL of t as text)
                    ignoring case
                        if (titleText contains queryText) or (urlText contains queryText) then
                            set active tab index of window w to tabIndex
                            set index of window w to 1
                            activate
                            set handled to true
                            exit repeat
                        end if
                    end ignoring
                end repeat
                if handled then exit repeat
            end repeat
        end if
        end tell
    end try
end if

if handled is false then
    try
        tell application "Microsoft Edge"
            if it is running then
                repeat with w from 1 to count of windows
                    set tabIndex to 0
                    repeat with t in tabs of window w
                        set tabIndex to tabIndex + 1
                        set titleText to (title of t as text)
                        set urlText to (URL of t as text)
                        ignoring case
                            if (titleText contains queryText) or (urlText contains queryText) then
                                set active tab index of window w to tabIndex
                                set index of window w to 1
                                activate
                                set handled to true
                                exit repeat
                            end if
                        end ignoring
                    end repeat
                    if handled then exit repeat
                end repeat
            end if
        end tell
    end try
end if

if handled is false then
    try
        tell application "Safari"
            if it is running then
                repeat with w from 1 to count of windows
                    set tabIndex to 0
                    repeat with t in tabs of window w
                        set tabIndex to tabIndex + 1
                        set titleText to (name of t as text)
                        set urlText to (URL of t as text)
                        ignoring case
                            if (titleText contains queryText) or (urlText contains queryText) then
                                set current tab of window w to t
                                set index of window w to 1
                                activate
                                set handled to true
                                exit repeat
                            end if
                        end ignoring
                    end repeat
                    if handled then exit repeat
                end repeat
            end if
        end tell
    end try
end if

if handled is false then
    error "No matching browser tab was found."
end if
"""
        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="focus_browser_tab",
            arguments={"query": normalized_query},
            result_summary=f"Focused the browser tab matching '{normalized_query}'.",
        )

    def open_browser_search(
        self,
        query: str,
        browser_name: str | None = None,
    ) -> ToolCallRecord:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Browser search query cannot be empty.")

        resolved_browser = self._resolve_browser_name(browser_name) if browser_name else None
        search_url = f"https://www.google.com/search?q={quote_plus(normalized_query)}"

        command = ["open", search_url]
        if resolved_browser:
            command = ["open", "-a", resolved_browser, search_url]

        self._run_process(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="open_browser_search",
            arguments={
                "query": normalized_query,
                "browser_name": browser_name or "",
                "resolved_browser": resolved_browser or "",
            },
            result_summary=(
                f"Opened a new browser search for '{normalized_query}'"
                f"{f' in {resolved_browser}' if resolved_browser else ''}."
            ),
        )

    def open_browser_search_for_user(
        self,
        user_id: str,
        *,
        query: str,
        browser_name: str | None = None,
    ) -> ToolCallRecord:
        return self.execute_capability(
            "browser",
            "search",
            user_id=user_id,
            requested_app=browser_name,
            query=query,
        )

    def delegate_to_codex(self, prompt: str) -> ToolCallRecord:
        workspace = str(self.workspace_root)
        command = f'cd {shell_quote(workspace)} && codex {shell_quote(prompt)}'
        script = f'''
tell application "Terminal"
    activate
    do script {json.dumps(command)}
end tell
'''
        self._run_process(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="delegate_to_codex",
            arguments={"prompt": prompt[:200]},
            result_summary="Opened Terminal and handed the task to Codex.",
        )

    def _youtube_javascript(self, action: str) -> str:
        media_command = {
            "pause": "if(video && !video.paused){video.pause();}",
            "play": "if(video && video.paused){video.play();}",
            "resume": "if(video && video.paused){video.play();}",
            "toggle": "if(video){ if(video.paused){video.play();} else {video.pause();}}",
        }[action]
        return (
            "(function(){"
            "const url = location.href || '';"
            "const video = document.querySelector('video');"
            "if(!/youtube\\.com|youtu\\.be/.test(url)){return 'not-youtube';}"
            "if(!video){return 'no-video';}"
            f"{media_command}"
            "return 'ok';"
            "})();"
        )

    def _browser_media_javascript(self, action: str) -> str:
        media_command = {
            "pause": "if(video && !video.paused){video.pause();}",
            "play": "if(video && video.paused){video.play();}",
            "resume": "if(video && video.paused){video.play();}",
            "toggle": "if(video){ if(video.paused){video.play();} else {video.pause();}}",
        }[action]
        return (
            "(function(){"
            "const candidates = Array.from(document.querySelectorAll('video'));"
            "const video = candidates.find((item) => item && (item.offsetWidth > 0 || item.offsetHeight > 0)) || candidates[0];"
            "if(!video){return 'no-video';}"
            f"{media_command}"
            "return 'ok';"
            "})();"
        )

    def _browser_javascript_script(self, js_command: str) -> str:
        return f"""
set handled to false
set jsCommand to {json.dumps(js_command)}

try
    tell application "Google Chrome"
        if it is running then
            set resultText to execute front window's active tab javascript jsCommand
            set handled to resultText is "ok"
        end if
    end tell
end try

if handled is false then
    try
        tell application "Arc"
            if it is running then
                set resultText to execute front window's active tab javascript jsCommand
                set handled to resultText is "ok"
            end if
        end tell
    end try
end if

if handled is false then
    try
        tell application "Brave Browser"
            if it is running then
                set resultText to execute front window's active tab javascript jsCommand
                set handled to resultText is "ok"
            end if
        end tell
    end try
end if

if handled is false then
    try
        tell application "Microsoft Edge"
            if it is running then
                set resultText to execute front window's active tab javascript jsCommand
                set handled to resultText is "ok"
            end if
        end tell
    end try
end if

if handled is false then
    try
        tell application "Safari"
            if it is running then
                set resultText to do JavaScript jsCommand in current tab of front window
                set handled to resultText is "ok"
            end if
        end tell
    end try
end if

if handled is false then
    error "No accessible matching video was found. Check the active tab and browser JavaScript automation permission."
end if
"""

    def _resolve_application_name(self, app_name: str) -> str | None:
        cleaned = self._sanitize_application_query(app_name)
        if not cleaned:
            return None

        for root in (Path("/System/Applications"), Path("/Applications"), Path.home() / "Applications"):
            if (root / f"{cleaned}.app").is_dir():
                return cleaned

        installed_apps = self._installed_application_names()
        lowered_cleaned = cleaned.lower()
        normalized_cleaned = self._normalize_app_name(cleaned)

        for installed in installed_apps:
            if installed.lower() == lowered_cleaned:
                return installed

        for installed in installed_apps:
            if self._normalize_app_name(installed) == normalized_cleaned:
                return installed

        spotlight_apps, _ = self.search_spotlight_applications(cleaned, limit=5)
        for app in spotlight_apps:
            if self._normalize_app_name(app) == normalized_cleaned:
                return app
        return None

    def _resolve_browser_name(self, browser_name: str | None) -> str | None:
        if not browser_name:
            return None
        cleaned = browser_name.strip()
        if not cleaned:
            return None
        aliases = {
            "arc": "Arc",
            "chrome": "Google Chrome",
            "google chrome": "Google Chrome",
            "brave": "Brave Browser",
            "edge": "Microsoft Edge",
            "microsoft edge": "Microsoft Edge",
            "safari": "Safari",
        }
        resolved = aliases.get(cleaned.lower(), cleaned)
        return self._resolve_application_name(resolved) or resolved

    def _execute_capability_on_app(
        self,
        capability_family: str,
        operation: str,
        app_name: str,
        **kwargs,
    ) -> ToolCallRecord:
        if capability_family == "browser":
            if operation == "search":
                return self.open_browser_search(kwargs["query"], browser_name=app_name)
            if operation == "focus_tab":
                return self.focus_browser_tab(kwargs["query"])
            if operation == "switch_tab":
                return self.switch_browser_tab(kwargs["direction"])
        if capability_family == "app":
            if operation == "open":
                return self.open_application(app_name)
            if operation == "activate":
                return self.activate_application(app_name)
        if capability_family == "email" and operation == "compose":
            if self._normalize_app_name(app_name) != self._normalize_app_name("Mail"):
                raise RuntimeError(f"{app_name} does not support compose yet.")
            return self.compose_email_draft(kwargs.get("recipient"), kwargs.get("subject"), kwargs.get("body"))
        if capability_family == "calendar" and operation == "create":
            if self._normalize_app_name(app_name) != self._normalize_app_name("Calendar"):
                raise RuntimeError(f"{app_name} does not support calendar creation yet.")
            return self.create_calendar_event(kwargs["title"], kwargs["start_at"], kwargs["end_at"])
        if capability_family == "document" and operation == "open_path":
            return self.open_path_in_application(kwargs["path"], app_name)
        if capability_family == "notes":
            if operation == "open":
                return self.open_application(app_name)
            if operation == "activate":
                return self.activate_application(app_name)
            if operation == "search":
                if self._normalize_app_name(app_name) != self._normalize_app_name("Notes"):
                    raise RuntimeError(f"{app_name} does not support note search yet.")
                return self.search_notes(kwargs["query"])
        if capability_family == "messaging":
            if operation == "open":
                return self.open_application(app_name)
            if operation == "activate":
                return self.activate_application(app_name)
            if operation == "draft":
                return self.draft_message(app_name, kwargs.get("recipient"), kwargs.get("body"))
        if capability_family == "codex" and operation == "delegate":
            return self.delegate_to_codex(kwargs["prompt"])
        if capability_family == "media_app" and operation == "control":
            if self._normalize_app_name(app_name) not in {
                self._normalize_app_name("Spotify"),
                self._normalize_app_name("Music"),
            }:
                raise RuntimeError(f"{app_name} does not support media app control.")
            return self.control_media_app(kwargs["action"], app_name=app_name)
        raise RuntimeError(f"Unsupported capability operation: {capability_family}.{operation}")

    def _resolve_capability_candidates(
        self,
        capability_family: str,
        operation: str,
        *,
        user_id: str,
        requested_app: str | None = None,
    ) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def add(value: str | None) -> None:
            if not value:
                return
            resolved = self._resolve_application_name(value) or self._resolve_alias_name(value) or value
            normalized = self._normalize_app_name(resolved)
            if normalized in seen:
                return
            if not self._supports_capability(resolved, capability_family, operation):
                return
            candidates.append(resolved)
            seen.add(normalized)

        if requested_app:
            add(requested_app)
            # Opening an explicitly named app must not launch an unrelated fallback app.
            if capability_family in {"app", "media_app"}:
                return candidates

        frontmost = self._frontmost_application_name()
        if frontmost and (not requested_app or self._normalize_app_name(frontmost) != self._normalize_app_name(requested_app)):
            add(frontmost)

        preferred = self._preference_value(user_id, self._capability_preference_key(capability_family))
        if preferred:
            add(preferred)

        for adapter in self.capability_adapters:
            if adapter.capability_family == capability_family and operation in adapter.operations:
                add(adapter.app_name)

        for running in self._running_application_names():
            add(running)

        return candidates

    def _verify_capability_execution(
        self,
        capability_family: str,
        operation: str,
        app_name: str,
        **kwargs,
    ) -> CapabilityVerification:
        if capability_family == "app" and operation == "open":
            # Launch Services already returned success; do not require System Events
            # automation permission just to open an app.
            return CapabilityVerification(True, f"macOS accepted the launch request for {app_name}; foreground state was not checked.")
        frontmost = self._frontmost_application_name()
        normalized_frontmost = self._normalize_app_name(frontmost or "")
        normalized_target = self._normalize_app_name(app_name)

        if capability_family in {"app", "email", "calendar", "notes", "messaging", "codex"}:
            if normalized_frontmost == normalized_target:
                return CapabilityVerification(True, f"{app_name} is frontmost.")
            return CapabilityVerification(False, f"{app_name} did not become frontmost.")

        if capability_family == "document":
            if normalized_frontmost == normalized_target:
                return CapabilityVerification(True, f"{app_name} is frontmost for the opened file.")
            return CapabilityVerification(False, f"{app_name} did not become frontmost for the file.")

        if capability_family == "browser":
            if normalized_frontmost != normalized_target:
                return CapabilityVerification(False, f"{app_name} did not become frontmost.")
            if operation == "search":
                current_url = self._browser_current_url(app_name)
                query = kwargs.get("query", "").strip().lower()
                if current_url and "google.com/search" in current_url.lower():
                    if not query or all(token in current_url.lower().replace("+", " ") for token in query.split()):
                        return CapabilityVerification(True, "Browser search page is active.")
                return CapabilityVerification(True, f"{app_name} is frontmost after opening the search.")
            return CapabilityVerification(True, f"{app_name} is frontmost.")

        if capability_family == "media_app":
            if normalized_frontmost == normalized_target:
                return CapabilityVerification(True, f"{app_name} is frontmost.")
            return CapabilityVerification(True, "Media command executed; playback state was not directly verified.")

        return CapabilityVerification(True, "Execution completed; no verifier was available.")

    def _supports_capability(self, app_name: str, capability_family: str, operation: str) -> bool:
        if capability_family == "app" and operation in {"open", "activate"}:
            return True
        normalized = self._normalize_app_name(app_name)
        for adapter in self.capability_adapters:
            aliases = {self._normalize_app_name(alias) for alias in adapter.aliases}
            if normalized == self._normalize_app_name(adapter.app_name) or normalized in aliases:
                if adapter.capability_family == capability_family and operation in adapter.operations:
                    return True
        return False

    def _resolve_alias_name(self, value: str) -> str | None:
        normalized = self._normalize_app_name(value)
        for adapter in self.capability_adapters:
            if normalized == self._normalize_app_name(adapter.app_name):
                return adapter.app_name
            for alias in adapter.aliases:
                if normalized == self._normalize_app_name(alias):
                    return adapter.app_name
        return None

    def _frontmost_application_name(self) -> str | None:
        script = """
tell application "System Events"
    set frontProcess to first application process whose frontmost is true
    return name of frontProcess
end tell
"""
        try:
            result = self._run_process(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:  # noqa: BLE001
            return None
        value = result.stdout.strip()
        return value or None

    def _browser_current_url(self, app_name: str) -> str | None:
        normalized = self._normalize_app_name(app_name)
        scripts = {
            self._normalize_app_name("Arc"): 'tell application "Arc" to return URL of active tab of front window',
            self._normalize_app_name("Google Chrome"): 'tell application "Google Chrome" to return URL of active tab of front window',
            self._normalize_app_name("Brave Browser"): 'tell application "Brave Browser" to return URL of active tab of front window',
            self._normalize_app_name("Microsoft Edge"): 'tell application "Microsoft Edge" to return URL of active tab of front window',
            self._normalize_app_name("Safari"): 'tell application "Safari" to return URL of current tab of front window',
        }
        script = scripts.get(normalized)
        if not script:
            return None
        try:
            result = self._run_process(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:  # noqa: BLE001
            return None
        value = result.stdout.strip()
        return value or None

    def _running_application_names(self) -> list[str]:
        script = """
tell application "System Events"
    set appNames to name of (application processes where background only is false)
    return appNames as text
end tell
"""
        try:
            result = self._run_process(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:  # noqa: BLE001
            return []
        raw = result.stdout.strip()
        if not raw:
            return []
        parts = [item.strip() for item in raw.split(",") if item.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for part in parts:
            normalized = self._normalize_app_name(part)
            if normalized not in seen:
                deduped.append(part)
                seen.add(normalized)
        return deduped

    def _preference_value(self, user_id: str, key: str | None) -> str | None:
        if not key:
            return None
        profile = self.profile_store.get_profile(user_id)
        for preference in profile.preferences:
            if preference.key == key:
                return preference.value
        return None

    def _capability_preference_key(self, capability_family: str) -> str | None:
        mapping = {
            "browser": "preferred_browser",
            "email": "preferred_mail_app",
            "calendar": "preferred_calendar_app",
            "document": "preferred_document_app",
            "messaging": "preferred_chat_app",
            "codex": "preferred_terminal_app",
            "media_app": "preferred_media_app",
        }
        return mapping.get(capability_family)

    def _sanitize_application_query(self, app_name: str) -> str:
        cleaned = app_name.strip().removesuffix(".app").strip()
        cleaned = cleaned.strip(" .,:;!?")
        cleaned = re.split(r"\b(?:in|on|from|using|with|for)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        return cleaned.strip(" .,:;!?")

    def _installed_application_names(self) -> list[str]:
        if self._installed_apps_cache is not None:
            return list(self._installed_apps_cache)
        roots = [
            Path("/Applications"),
            Path.home() / "Applications",
            Path("/System/Applications"),
        ]
        names: list[str] = []
        seen: set[str] = set()
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.app"):
                name = path.stem
                if name not in seen:
                    names.append(name)
                    seen.add(name)
        self._installed_apps_cache = names
        return list(names)

    def _normalize_app_name(self, value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())

    def _spotlight_paths(self, query: str, *, limit: int, home_only: bool = True) -> list[str]:
        command = ["mdfind"]
        if home_only:
            command.extend(["-onlyin", str(Path.home())])
        command.append(query)
        result = self._run_process(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if path not in seen:
                deduped.append(path)
                seen.add(path)
            if len(deduped) >= limit:
                break
        return deduped

    def _rank_spotlight_file_paths(
        self,
        query: str,
        paths: list[str],
        *,
        limit: int,
        prefer_recent: bool,
    ) -> list[str]:
        ranked = sorted(
            (Path(path) for path in paths),
            key=lambda path: self._spotlight_file_score(query, path, prefer_recent=prefer_recent),
            reverse=True,
        )
        filtered = [str(path) for path in ranked if self._should_keep_spotlight_path(path)]
        return filtered[:limit]

    def _spotlight_file_score(self, query: str, path: Path, *, prefer_recent: bool) -> int:
        score = 0
        lowered_query = query.lower().strip()
        tokens = [token for token in re.split(r"\s+", lowered_query) if token]
        path_str = str(path).lower()
        name = path.name.lower()
        stem = path.stem.lower()

        if path.suffix.lower() in {
            ".pdf", ".txt", ".md", ".rtf", ".doc", ".docx", ".pages",
            ".png", ".jpg", ".jpeg", ".webp", ".csv", ".json", ".pptx",
            ".key", ".xlsx", ".numbers",
        }:
            score += 18

        if path.suffix.lower() in {
            ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".c", ".cpp", ".h",
            ".swift", ".java", ".kt", ".rs", ".go", ".toml", ".yml", ".yaml",
            ".lock",
        }:
            score -= 35

        if self.workspace_root in path.parents:
            score += 70

        home = Path.home()
        preferred_roots = [
            home / "Desktop",
            home / "Documents",
            home / "Downloads",
            home / "Movies",
            home / "Pictures",
        ]
        for root in preferred_roots:
            if root == path.parent or root in path.parents:
                score += 28
                break

        if lowered_query:
            if lowered_query == stem:
                score += 90
            elif lowered_query == name:
                score += 80
            elif lowered_query in stem:
                score += 55
            elif lowered_query in name:
                score += 45
            elif lowered_query in path_str:
                score += 20

        for token in tokens:
            if token in stem:
                score += 18
            elif token in name:
                score += 14
            elif token in path_str:
                score += 6

        if prefer_recent:
            score += 15

        noisy_markers = [
            "site-packages",
            "node_modules",
            ".venv",
            "miniforge",
            "cache",
            "jupyter",
            "notebook/static",
            "__pycache__",
            ".git",
            "deriveddata",
            ".egg-info",
        ]
        if any(marker in path_str for marker in noisy_markers):
            score -= 80

        return score

    def _should_keep_spotlight_path(self, path: Path) -> bool:
        path_str = str(path).lower()
        blocked_markers = [
            "/site-packages/",
            "/node_modules/",
            "/__pycache__/",
            "/.git/",
            "/library/caches/",
            "/deriveddata/",
            ".egg-info/",
        ]
        if any(marker in path_str for marker in blocked_markers):
            return False
        return path.is_file()

    def _mdfind_escape(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _datetime_components(self, value: datetime) -> dict[str, int]:
        return {
            "year": value.year,
            "month": value.month,
            "day": value.day,
            "seconds": value.hour * 3600 + value.minute * 60 + value.second,
        }

    def _resolve_user_path(self, raw_path: str, *, allow_missing: bool = False) -> Path:
        expanded = Path(raw_path).expanduser()
        candidates: list[Path] = []
        if expanded.is_absolute():
            candidates.append(expanded)
        else:
            candidates.append((self.workspace_root / expanded).resolve())
            candidates.append((Path.home() / expanded).resolve())
            candidates.append(expanded.resolve())

        for candidate in candidates:
            if candidate.exists():
                return candidate

        if allow_missing:
            candidate = candidates[0]
            if self.workspace_root not in candidate.parents and candidate != self.workspace_root and Path.home() not in candidate.parents:
                raise ValueError("Destination path is outside supported locations.")
            return candidate

        raise FileNotFoundError(f"Path not found: {raw_path}")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
