#!/bin/zsh
set -euo pipefail

APP_NAME="Cleo.app"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_CONFIGURATION="${CLEO_BUILD_CONFIGURATION:-debug}"
SWIFT_BUILD_FLAGS="${CLEO_SWIFT_BUILD_FLAGS:---disable-index-store}"
CLEAN_BEFORE_BUILD="${CLEO_CLEAN_BEFORE_BUILD:-1}"
if [[ "$BUILD_CONFIGURATION" != "debug" && "$BUILD_CONFIGURATION" != "release" ]]; then
  echo "Unsupported CLEO_BUILD_CONFIGURATION: $BUILD_CONFIGURATION"
  echo "Use 'debug' or 'release'."
  exit 1
fi

LOCK_DIR="$ROOT_DIR/.build_app.lock"
BUILD_DIR="$ROOT_DIR/.build/$BUILD_CONFIGURATION"
APP_DIR="$ROOT_DIR/dist/$APP_NAME"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
LAUNCHER_DIR="$RESOURCES_DIR/CleoRuntime"
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$ROOT_DIR/AppBundle/Info.plist")"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another Cleo app build is already running."
  echo "If that is stale, stop it and remove:"
  echo "  $LOCK_DIR"
  exit 1
fi

cleanup() {
  rm -rf "$LOCK_DIR"
}

trap cleanup EXIT

remove_tree() {
  local target_path="$1"
  [[ -e "$target_path" ]] || return 0
  chmod -R u+w "$target_path" 2>/dev/null || true
  chflags -R nouchg "$target_path" 2>/dev/null || true
  xattr -cr "$target_path" 2>/dev/null || true
  /bin/rm -rf "$target_path" 2>/dev/null || true
}

copy_clean() {
  local source_path="$1"
  local destination_path="$2"
  COPYFILE_DISABLE=1 cp -X "$source_path" "$destination_path"
  xattr -c "$destination_path" 2>/dev/null || true
}

if [[ "$CLEAN_BEFORE_BUILD" == "1" ]]; then
  echo "Cleaning previous desktop app build artifacts..."
  remove_tree "$ROOT_DIR/.build"
  remove_tree "$ROOT_DIR/dist"
fi

mkdir -p "$ROOT_DIR/.build"

echo "Building CleoOverlay in $BUILD_CONFIGURATION mode..."
cd "$ROOT_DIR"
swift build -c "$BUILD_CONFIGURATION" ${=SWIFT_BUILD_FLAGS}

echo "Creating app bundle..."
mkdir -p "$ROOT_DIR/dist"
find "$ROOT_DIR/dist" -maxdepth 1 -name 'Cleo*.app' -prune -exec rm -rf {} +
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR" "$LAUNCHER_DIR"

find "$ROOT_DIR/AppBundle" -name '.DS_Store' -delete
xattr -cr "$ROOT_DIR/AppBundle" "$BUILD_DIR/CleoOverlay" 2>/dev/null || true

copy_clean "$ROOT_DIR/AppBundle/Info.plist" "$CONTENTS_DIR/Info.plist"
copy_clean "$BUILD_DIR/CleoOverlay" "$MACOS_DIR/CleoOverlay"
chmod +x "$MACOS_DIR/CleoOverlay"

if [[ -f "$ROOT_DIR/AppBundle/Resources/Cleo.icns" ]]; then
  copy_clean "$ROOT_DIR/AppBundle/Resources/Cleo.icns" "$RESOURCES_DIR/Cleo.icns"
fi

if [[ -f "$ROOT_DIR/AppBundle/Assets/Cleo-icon-source.png" ]]; then
  copy_clean "$ROOT_DIR/AppBundle/Assets/Cleo-icon-source.png" "$RESOURCES_DIR/CleoIcon.png"
fi

if [[ -f "$ROOT_DIR/AppBundle/Assets/Cleo-mark-source.png" ]]; then
  copy_clean "$ROOT_DIR/AppBundle/Assets/Cleo-mark-source.png" "$RESOURCES_DIR/CleoMark.png"
fi

cat > "$LAUNCHER_DIR/run_bridge.sh" <<'EOF'
#!/bin/zsh
set -euo pipefail

APP_SUPPORT_DIR="$HOME/Library/Application Support/Cleo"
RUNTIME_ROOT="${CLEO_RUNTIME_ROOT:-$APP_SUPPORT_DIR/runtime}"
RUNTIME_LAUNCHER="$RUNTIME_ROOT/run_bridge.sh"

if [[ ! -x "$RUNTIME_LAUNCHER" ]]; then
  echo "Cleo runtime is not installed yet." >&2
  echo "Install or refresh the local runtime with Cleo's install_runtime.sh script." >&2
  exit 1
fi

exec "$RUNTIME_LAUNCHER" "$@"
EOF
chmod +x "$LAUNCHER_DIR/run_bridge.sh"
xattr -cr "$LAUNCHER_DIR" 2>/dev/null || true

echo "Signing app bundle with identifier $BUNDLE_ID..."
xattr -cr "$APP_DIR" 2>/dev/null || true
codesign --force --deep --sign - --identifier "$BUNDLE_ID" "$APP_DIR"
codesign -dv --verbose=2 "$APP_DIR" >/dev/null 2>&1

echo "Built app bundle at:"
echo "  $APP_DIR"
echo ""
echo "The desktop app build is now lightweight."
echo "Install or refresh the local runtime separately with:"
echo "  \"$ROOT_DIR/scripts/install_runtime.sh\""
echo ""
echo "You can launch it with:"
echo "  open \"$APP_DIR\""
