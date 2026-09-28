#!/bin/zsh
set -euo pipefail
APP_SUPPORT_DIR="${CLEO_APP_SUPPORT_DIR:-$HOME/Library/Application Support/Cleo}"
RUNTIME_ROOT="${CLEO_RUNTIME_ROOT:-$APP_SUPPORT_DIR/runtime}"
PYTHON_BIN="$APP_SUPPORT_DIR/venv/bin/python3"
[[ -x "$PYTHON_BIN" ]] || exit 0
"$PYTHON_BIN" - "$RUNTIME_ROOT/bridge/local_bridge.py" <<'PY'
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

bridge = Path(sys.argv[1])
output = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True)
targets = []
for line in output.splitlines():
    parts = line.strip().split(None, 1)
    if len(parts) != 2:
        continue
    pid, command = parts
    # Match only this installed bridge, never other Python servers or apps.
    python_paths = (sys.executable, str(bridge.parent.parent / "bin/python3"))
    if command.startswith(tuple(f"{path} " for path in python_paths)) and f" {bridge} serve --port 8765" in command and int(pid) != os.getpid():
        try:
            os.kill(int(pid), signal.SIGTERM)
            targets.append(int(pid))
        except ProcessLookupError:
            pass
deadline = time.monotonic() + 5
while targets and time.monotonic() < deadline:
    for pid in targets[:]:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            targets.remove(pid)
    time.sleep(0.1)
if targets:
    raise SystemExit("The previous Cleo runtime did not stop. Quit Cleo before retrying installation.")
if targets == []:
    print("Previous Cleo runtime stopped (or none was running).")
PY
