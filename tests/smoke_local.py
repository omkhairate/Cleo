"""Exercise real local models without launching apps or changing user memory."""
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from test_runtime import bridge
from assistant_core.config import Settings
from assistant_core.models import ToolCallRecord
from assistant_core.services.orchestrator import AssistantOrchestrator
from PIL import Image
from urllib.request import Request, urlopen
import threading


def main():
    with tempfile.TemporaryDirectory() as directory:
        settings = Settings(_env_file=None, state_file_path=str(Path(directory) / "state.json"), argus_enabled=False)
        with patch("assistant_core.services.orchestrator.get_settings", return_value=settings):
            assistant = AssistantOrchestrator()
        assistant.warmup_runtime(text_only=False)
        image = Path(directory) / "red.png"
        Image.new("RGB", (128, 128), "red").save(image)
        requests = [
            ("chat", {"message": "Hello"}, "transformers-text"),
            ("selection", {"message": "What does this word mean?", "visual_context": {"source": "window-context", "selected_text": "water", "ocr_text": "unrelated content", "image_path": str(image)}}, "transformers-text"),
            ("visual", {"message": "What is the main color in this image?", "visual_context": {"source": "window-context", "image_path": str(image)}}, "transformers-smolvlm"),
            ("action", {"message": "Open Music"}, "action-specialist"),
        ]
        server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.CleoBridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "_ORCHESTRATOR", assistant), patch.object(
            assistant.command_tools, "open_application",
            return_value=ToolCallRecord(tool_name="open_application", arguments={"app_name": "Music"}, result_summary="Opened Music (smoke test stub)."),
        ), patch.object(
            assistant.command_tools, "_frontmost_application_name", return_value="Music",
        ):
            thread.start()
            try:
                for name, payload, provider in requests:
                    start = time.monotonic()
                    request = Request(f"http://127.0.0.1:{server.server_port}/interact/stream", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                    with urlopen(request, timeout=120) as response:
                        events = [json.loads(line) for line in response]
                    final = events[-1]
                    if final["type"] != "final" or not final.get("provider"):
                        raise RuntimeError(f"{name} failed: {final}")
                    if name != "action" and final["provider"] != provider:
                        raise RuntimeError(f"{name} used the wrong model: {final}")
                    print(json.dumps({"route": name, "seconds": round(time.monotonic() - start, 2), "provider": final["provider"], "response": final["response"]}), flush=True)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    main()
