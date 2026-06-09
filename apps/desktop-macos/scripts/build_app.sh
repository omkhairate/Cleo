#!/bin/zsh
set -euo pipefail

APP_NAME="Cleo.app"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/.build/release"
APP_DIR="$ROOT_DIR/dist/$APP_NAME"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

echo "Building CleoOverlay in release mode..."
cd "$ROOT_DIR"
swift build -c release

echo "Creating app bundle..."
rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

cp "$ROOT_DIR/AppBundle/Info.plist" "$CONTENTS_DIR/Info.plist"
cp "$BUILD_DIR/CleoOverlay" "$MACOS_DIR/CleoOverlay"
chmod +x "$MACOS_DIR/CleoOverlay"

if [[ -f "$ROOT_DIR/AppBundle/Resources/Cleo.icns" ]]; then
  cp "$ROOT_DIR/AppBundle/Resources/Cleo.icns" "$RESOURCES_DIR/Cleo.icns"
fi

if [[ -f "$ROOT_DIR/AppBundle/Assets/Cleo-icon-source.png" ]]; then
  cp "$ROOT_DIR/AppBundle/Assets/Cleo-icon-source.png" "$RESOURCES_DIR/CleoIcon.png"
fi

if [[ -f "$ROOT_DIR/AppBundle/Assets/Cleo-mark-source.png" ]]; then
  cp "$ROOT_DIR/AppBundle/Assets/Cleo-mark-source.png" "$RESOURCES_DIR/CleoMark.png"
fi

echo "Built app bundle at:"
echo "  $APP_DIR"
echo ""
echo "You can launch it with:"
echo "  open \"$APP_DIR\""
