#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")


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


def _read_request_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length > 0 else b""
    return json.loads(raw.decode("utf-8")) if raw else {}


def _write_json(handler: BaseHTTPRequestHandler, payload: dict | list, status: int = 200) -> None:
    data = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _get_orchestrator():
    global _ORCHESTRATOR
    with _ORCHESTRATOR_LOCK:
        if _ORCHESTRATOR is None:
            from assistant_core.services.orchestrator import AssistantOrchestrator

            _ORCHESTRATOR = AssistantOrchestrator()
    return _ORCHESTRATOR


_ORCHESTRATOR = None
_ORCHESTRATOR_LOCK = threading.Lock()
_SERVING_REVISION = None
_PROACTIVITY = None
_PROACTIVITY_LOCK = threading.Lock()
_FILE_EVIDENCE = None


def _handle_file_access(payload):
    global _FILE_EVIDENCE
    from assistant_core.config import get_settings
    from assistant_core.services.file_evidence import FileEvidenceService
    with _PROACTIVITY_LOCK:
        if _FILE_EVIDENCE is None:
            path = Path(get_settings().state_file_path).expanduser().resolve().with_name("file-evidence.sqlite3")
            _FILE_EVIDENCE = FileEvidenceService(path)
    return _FILE_EVIDENCE.handle(payload)


def _handle_proactivity(payload):
    global _PROACTIVITY
    from assistant_core.config import get_settings
    from assistant_core.services.proactivity import ProactivityService
    with _PROACTIVITY_LOCK:
        if _PROACTIVITY is None:
            path = Path(get_settings().state_file_path).expanduser().resolve().with_name("proactivity.sqlite3")
            _PROACTIVITY = ProactivityService(path)
    return _PROACTIVITY.handle(payload)


def _runtime_revision() -> str:
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    spec = importlib.util.find_spec("assistant_core")
    if spec and spec.origin:
        root = Path(spec.origin).parent
        for path in sorted(root.rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _handle_command(command: str, payload: dict) -> tuple[int, dict]:
    from assistant_core.models import ChatGPTImportRequest, ContextPackBuildRequest, DeviceUpsertRequest, InteractionRequest

    orchestrator = _get_orchestrator()
    if command == "interact":
        request = InteractionRequest.model_validate(payload)
        reply = orchestrator.interact(request)
        return 200, reply.model_dump(mode="json")
    if command == "memory_snapshot":
        return 200, orchestrator.get_workspace_memory_snapshot("local-user").model_dump(mode="json")
    if command == "import_chatgpt":
        request = ChatGPTImportRequest.model_validate(payload)
        reply = orchestrator.import_chatgpt_export(request)
        return 200, reply.model_dump(mode="json")
    if command == "build_context_pack":
        request = ContextPackBuildRequest.model_validate(payload)
        reply = orchestrator.build_context_pack(request)
        return 200, reply.model_dump(mode="json")
    if command == "model_status":
        return 200, orchestrator.get_model_status()
    if command == "warmup":
        text_only = bool(payload.get("text_only", True))
        return 200, orchestrator.warmup_runtime(text_only=text_only)
    if command == "list_devices":
        user_id = payload.get("user_id", "local-user")
        return 200, [device.model_dump(mode="json") for device in orchestrator.list_devices(user_id)]
    if command == "upsert_device":
        request = DeviceUpsertRequest.model_validate(payload)
        reply = orchestrator.upsert_device(request)
        return 200, reply.model_dump(mode="json")
    raise ValueError(f"Unknown command: {command}")


class CleoBridgeHandler(BaseHTTPRequestHandler):
    server_version = "CleoBridge/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/capability-status":
            try:
                _write_json(self, _get_orchestrator().get_capability_status())
            except Exception as exc:
                _write_json(self, {"error": str(exc)}, status=500)
            return
        if self.path == "/health":
            _write_json(self, {"status": "ok", "runtime_revision": _SERVING_REVISION})
            return
        if self.path in {"/model-status", "/devices"}:
            try:
                command = "model_status" if self.path == "/model-status" else "list_devices"
                _, payload = _handle_command(command, {})
                _write_json(self, payload)
            except Exception as exc:  # noqa: BLE001
                _write_json(self, {"error": str(exc)}, status=500)
            return
        _write_json(self, {"error": "Not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/file-access":
                try:
                    _write_json(self, _handle_file_access(_read_request_json(self)))
                except ValueError as exc:
                    _write_json(self, {"error": str(exc)}, status=400)
                return
            if self.path == "/proactivity":
                try:
                    _write_json(self, _handle_proactivity(_read_request_json(self)))
                except ValueError as exc:
                    _write_json(self, {"error": str(exc)}, status=400)
                return
            if self.path == "/interact":
                payload = _read_request_json(self)
                _, response_payload = _handle_command("interact", payload)
                _write_json(self, response_payload)
                return
            if self.path == "/memory-snapshot":
                _, response_payload = _handle_command("memory_snapshot", {})
                _write_json(self, response_payload)
                return
            if self.path == "/imports/chatgpt":
                payload = _read_request_json(self)
                _, response_payload = _handle_command("import_chatgpt", payload)
                _write_json(self, response_payload)
                return
            if self.path == "/context-packs":
                payload = _read_request_json(self)
                _, response_payload = _handle_command("build_context_pack", payload)
                _write_json(self, response_payload)
                return
            if self.path == "/devices":
                payload = _read_request_json(self)
                _, response_payload = _handle_command("upsert_device", payload)
                _write_json(self, response_payload)
                return
            if self.path == "/warmup":
                payload = _read_request_json(self)
                _, response_payload = _handle_command("warmup", payload)
                _write_json(self, response_payload)
                return
            if self.path == "/interact/stream":
                self._stream_interaction()
                return
            _write_json(self, {"error": "Not found"}, status=404)
        except Exception as exc:  # noqa: BLE001
            _write_json(self, {"error": str(exc)}, status=500)

    def _stream_interaction(self) -> None:
        from assistant_core.models import InteractionRequest

        payload = _read_request_json(self)
        request = InteractionRequest.model_validate(payload)
        orchestrator = _get_orchestrator()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        self.close_connection = True
        events = orchestrator.stream_interaction_events(request)
        try:
            for event in events:
                self.wfile.write((json.dumps(event, default=str) + "\n").encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            # Headers are already sent; errors must remain part of the NDJSON stream.
            try:
                event = {"type": "error", "response": str(exc)}
                self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            events.close()


def _serve(port: int, parent_pid: int | None = None) -> int:
    global _SERVING_REVISION
    _bootstrap_paths()
    # Freeze at startup, so updated files cannot disguise stale imported modules.
    _SERVING_REVISION = _runtime_revision()
    server = ThreadingHTTPServer(("127.0.0.1", port), CleoBridgeHandler)
    if parent_pid:
        def watch_parent() -> None:
            while True:
                time.sleep(1)
                try:
                    os.kill(parent_pid, 0)
                except ProcessLookupError:
                    server.shutdown()
                    return
                except PermissionError:
                    continue
        threading.Thread(target=watch_parent, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    _bootstrap_paths()

    if len(sys.argv) < 2:
        print("Usage: local_bridge.py <command>", file=sys.stderr)
        return 2

    command = sys.argv[1]

    if command == "revision":
        print(_runtime_revision())
        return 0

    if command == "serve":
        port = 8765
        if len(sys.argv) >= 4 and sys.argv[2] == "--port":
            port = int(sys.argv[3])
        parent_pid = None
        if "--parent-pid" in sys.argv:
            parent_pid = int(sys.argv[sys.argv.index("--parent-pid") + 1])
        return _serve(port, parent_pid)

    try:
        payload = _read_stdin_json()
        _, response_payload = _handle_command(command, payload)
        print(json.dumps(response_payload, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
