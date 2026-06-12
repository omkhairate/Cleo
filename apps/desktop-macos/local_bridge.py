#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _bootstrap_paths() -> None:
    if app_packages := os.environ.get("CLEO_APP_PACKAGES"):
        sys.path.insert(0, app_packages)
        return

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "packages" / "assistant-core" / "src"))
    sys.path.insert(0, str(root / "apps" / "api" / "src"))


def _read_stdin_json() -> dict:
    raw = sys.stdin.read().strip()
    return json.loads(raw) if raw else {}


def main() -> int:
    _bootstrap_paths()

    from assistant_core.models import ChatGPTImportRequest, InteractionRequest
    from assistant_core.services.orchestrator import AssistantOrchestrator

    if len(sys.argv) < 2:
        print("Usage: local_bridge.py <command>", file=sys.stderr)
        return 2

    command = sys.argv[1]
    orchestrator = AssistantOrchestrator()

    try:
        if command == "interact":
            payload = _read_stdin_json()
            request = InteractionRequest.model_validate(payload)
            reply = orchestrator.interact(request)
            print(reply.model_dump_json())
            return 0

        if command == "interact_stream":
            payload = _read_stdin_json()
            request = InteractionRequest.model_validate(payload)
            for event in orchestrator.stream_interaction_events(request):
                print(json.dumps(event, default=str), flush=True)
            return 0

        if command == "memory_snapshot":
            user_id = "local-user"
            payload = {
                "profile": orchestrator.get_profile(user_id).model_dump(mode="json"),
                "graph": orchestrator.get_brain_graph().model_dump(mode="json"),
                "imports": [item.model_dump(mode="json") for item in orchestrator.get_import_history()],
            }
            print(json.dumps(payload, default=str))
            return 0

        if command == "import_chatgpt":
            payload = _read_stdin_json()
            request = ChatGPTImportRequest.model_validate(payload)
            reply = orchestrator.import_chatgpt_export(request)
            print(reply.model_dump_json())
            return 0

        print(f"Unknown command: {command}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
