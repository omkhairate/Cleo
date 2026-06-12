#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$ROOT_DIR/../.." && pwd)"
APP_SUPPORT_DIR="${CLEO_APP_SUPPORT_DIR:-$HOME/Library/Application Support/Cleo}"
TEMP_CAPTURE_DIR="${TMPDIR:-/tmp}cleo-pointer-captures"

remove_tree() {
  local target_path="$1"
  [[ -e "$target_path" ]] || return 0
  chmod -R u+w "$target_path" 2>/dev/null || true
  chflags -R nouchg "$target_path" 2>/dev/null || true
  xattr -cr "$target_path" 2>/dev/null || true
  /bin/rm -rf "$target_path" 2>/dev/null || true
  if [[ -e "$target_path" ]]; then
    python3 - "$target_path" <<'PY'
import os
import shutil
import sys

path = sys.argv[1]
if os.path.lexists(path):
    shutil.rmtree(path, ignore_errors=True)
PY
  fi
}

echo "Stopping running Cleo processes..."
pkill -f '/Cleo.app/Contents/MacOS/CleoOverlay' 2>/dev/null || true
pkill -f '/dist/Cleo.app/Contents/MacOS/CleoOverlay' 2>/dev/null || true
pkill -f '/dist/Cleo 2.app/Contents/MacOS/CleoOverlay' 2>/dev/null || true
pkill -f 'local_bridge.py' 2>/dev/null || true
pkill -f 'cleo_api.main:app' 2>/dev/null || true

echo "Removing local macOS app build remnants..."
remove_tree "$ROOT_DIR/.build"
remove_tree "$ROOT_DIR/dist"
remove_tree "$ROOT_DIR/__pycache__"
remove_tree "$ROOT_DIR/.swiftpm"
find "$ROOT_DIR" -maxdepth 3 -name '.DS_Store' -delete

echo "Removing standalone runtime remnants..."
remove_tree "$APP_SUPPORT_DIR/runtime"

echo "Removing pointer capture cache..."
remove_tree "$TEMP_CAPTURE_DIR"

echo "Removing Python cache remnants..."
find "$PROJECT_ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PROJECT_ROOT" \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

echo "Cleanup complete."
echo "Next steps:"
echo "  1. $ROOT_DIR/scripts/install_runtime.sh"
echo "  2. $ROOT_DIR/scripts/build_app.sh"
echo "  3. open \"$ROOT_DIR/dist/Cleo.app\""
