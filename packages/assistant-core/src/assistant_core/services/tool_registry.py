from pathlib import Path
import json
import re
import subprocess

from assistant_core.config import Settings
from assistant_core.models import ToolCallRecord, UserProfile
from assistant_core.services.memory import InMemoryBrainGraphStore, InMemoryConversationStore, InMemoryProfileStore


class CommandToolRegistry:
    """Small set of deterministic tools specialists can call."""

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
        resolved_app_name = self._resolve_application_name(app_name) or app_name
        subprocess.run(
            ["open", "-a", resolved_app_name],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="open_application",
            arguments={"app_name": app_name, "resolved_app_name": resolved_app_name},
            result_summary=f"Opened macOS application '{resolved_app_name}'.",
        )

    def control_youtube_playback(self, action: str) -> ToolCallRecord:
        normalized = action.lower().strip()
        if normalized not in {"pause", "play", "resume", "toggle"}:
            raise ValueError(f"Unsupported YouTube playback action: {action}")

        script = self._browser_javascript_script(self._youtube_javascript(normalized))

        subprocess.run(
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
        subprocess.run(
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

    def control_media_app(self, action: str) -> ToolCallRecord:
        normalized = action.lower().strip()
        if normalized not in {"pause", "play", "resume", "toggle", "next", "previous"}:
            raise ValueError(f"Unsupported media action: {action}")

        spotify_command = {
            "pause": "pause",
            "play": "play",
            "resume": "play",
            "toggle": "playpause",
            "next": "next track",
            "previous": "previous track",
        }[normalized]
        music_command = spotify_command

        script = f"""
set handled to false
try
    tell application "Spotify"
        if it is running then
            {spotify_command}
            set handled to true
        end if
    end tell
end try

if handled is false then
    try
        tell application "Music"
            if it is running then
                {music_command}
                set handled to true
            end if
        end tell
    end try
end if

if handled is false then
    error "No supported media app is currently running."
end if
"""

        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return ToolCallRecord(
            tool_name="control_media_app",
            arguments={"action": normalized},
            result_summary=f"Sent a {normalized} command to the active media app.",
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

        subprocess.run(
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

    def delegate_to_codex(self, prompt: str) -> ToolCallRecord:
        workspace = str(self.workspace_root)
        command = f'cd {shell_quote(workspace)} && codex {shell_quote(prompt)}'
        script = f'''
tell application "Terminal"
    activate
    do script {json.dumps(command)}
end tell
'''
        subprocess.run(
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
            execute front window's active tab javascript jsCommand
            set handled to true
        end if
    end tell
end try

if handled is false then
    try
        tell application "Arc"
            if it is running then
                execute front window's active tab javascript jsCommand
                set handled to true
            end if
        end tell
    end try
end if

if handled is false then
    try
        tell application "Brave Browser"
            if it is running then
                execute front window's active tab javascript jsCommand
                set handled to true
            end if
        end tell
    end try
end if

if handled is false then
    try
        tell application "Microsoft Edge"
            if it is running then
                execute front window's active tab javascript jsCommand
                set handled to true
            end if
        end tell
    end try
end if

if handled is false then
    try
        tell application "Safari"
            if it is running then
                do JavaScript jsCommand in current tab of front window
                set handled to true
            end if
        end tell
    end try
end if

if handled is false then
    error "No supported browser is currently running."
end if
"""

    def _resolve_application_name(self, app_name: str) -> str | None:
        cleaned = self._sanitize_application_query(app_name)
        if not cleaned:
            return None

        direct_candidates = [cleaned, f"{cleaned}.app"]
        for candidate in direct_candidates:
            probe = subprocess.run(
                ["open", "-Ra", candidate],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
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

        for installed in installed_apps:
            normalized_installed = self._normalize_app_name(installed)
            if normalized_installed.startswith(normalized_cleaned) or normalized_cleaned.startswith(normalized_installed):
                return installed

        return None

    def _sanitize_application_query(self, app_name: str) -> str:
        cleaned = app_name.strip().removesuffix(".app").strip()
        cleaned = cleaned.strip(" .,:;!?")
        cleaned = re.split(r"\b(?:in|on|from|using|with|for)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        return cleaned.strip(" .,:;!?")

    def _installed_application_names(self) -> list[str]:
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
        return names

    def _normalize_app_name(self, value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
