#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="$ROOT_DIR/dist/Cleo.app"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App bundle not found. Building it first..."
  "$ROOT_DIR/scripts/build_app.sh"
fi

open "$APP_PATH"
