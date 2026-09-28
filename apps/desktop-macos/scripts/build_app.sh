#!/bin/zsh
set -euo pipefail

APP_NAME="Cleo.app"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_CONFIGURATION="${CLEO_BUILD_CONFIGURATION:-debug}"
SWIFT_BUILD_FLAGS="${CLEO_SWIFT_BUILD_FLAGS:---disable-index-store}"
CLEAN_BEFORE_BUILD="${CLEO_CLEAN_BEFORE_BUILD:-0}"
SCRATCH_DIR="${CLEO_SWIFT_SCRATCH_DIR:-$HOME/Library/Caches/Cleo/swift-build}"
if [[ "$BUILD_CONFIGURATION" != "debug" && "$BUILD_CONFIGURATION" != "release" ]]; then
  echo "Unsupported CLEO_BUILD_CONFIGURATION: $BUILD_CONFIGURATION"
  echo "Use 'debug' or 'release'."
  exit 1
fi

LOCK_DIR="$ROOT_DIR/.build_app.lock"
BUILD_DIR="$SCRATCH_DIR/$BUILD_CONFIGURATION"
DIST_APP_DIR="$ROOT_DIR/dist/$APP_NAME"
INSTALLED_APP_DIR="${CLEO_INSTALL_DIR:-$HOME/Applications}/$APP_NAME"
STAGING_DIR=""
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$ROOT_DIR/AppBundle/Info.plist")"
SIGNING_IDENTITY="${CLEO_CODE_SIGN_IDENTITY:-}"
if [[ "$SIGNING_IDENTITY" == "-" ]]; then
  echo "Ad-hoc signing cannot preserve Cleo's identity across rebuilds."
  echo "Set CLEO_CODE_SIGN_IDENTITY to a reusable signing certificate, or leave it unset."
  exit 1
fi
if [[ -z "$SIGNING_IDENTITY" ]]; then
  "$ROOT_DIR/scripts/setup_signing.sh"
  SIGNING_DIR="${CLEO_SIGNING_DIR:-$HOME/Library/Application Support/Cleo/signing}"
  SIGNING_IDENTITY="$(openssl x509 -in "$SIGNING_DIR/identity.pem" -noout -fingerprint -sha1 | sed 's/.*=//; s/://g')"
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another Cleo app build is already running."
  echo "If that is stale, stop it and remove:"
  echo "  $LOCK_DIR"
  exit 1
fi

cleanup() {
  [[ -z "$STAGING_DIR" ]] || rm -rf "$STAGING_DIR"
  rm -rf "$LOCK_DIR"
}

trap cleanup EXIT

# File Provider can reattach FinderInfo to bundles under Desktop while signing.
mkdir -p "$HOME/Library/Caches/Cleo"
STAGING_DIR="$(mktemp -d "$HOME/Library/Caches/Cleo/app-bundle.XXXXXX")"
APP_DIR="$STAGING_DIR/$APP_NAME"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
LAUNCHER_DIR="$RESOURCES_DIR/CleoRuntime"

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
  remove_tree "$SCRATCH_DIR"
  remove_tree "$ROOT_DIR/dist"
fi

mkdir -p "$ROOT_DIR/.build"

echo "Building CleoOverlay in $BUILD_CONFIGURATION mode..."
cd "$ROOT_DIR"
swift build --scratch-path "$SCRATCH_DIR" -c "$BUILD_CONFIGURATION" ${=SWIFT_BUILD_FLAGS}

echo "Creating app bundle..."
mkdir -p "$ROOT_DIR/dist"
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
codesign --force --sign "$SIGNING_IDENTITY" --timestamp=none --identifier "$BUNDLE_ID" "$APP_DIR"
codesign --verify --strict "$APP_DIR"
codesign -dv --verbose=2 "$APP_DIR" >/dev/null 2>&1

echo "Installing signed app outside Desktop..."
"$ROOT_DIR/scripts/publish_app.sh" "$APP_DIR" "$INSTALLED_APP_DIR" "$DIST_APP_DIR"

echo "Built app bundle at:"
echo "  $INSTALLED_APP_DIR"
echo "Compatibility shortcut: $DIST_APP_DIR"
echo ""
echo "The desktop app build is now lightweight."
echo "Install or refresh the local runtime separately with:"
echo "  \"$ROOT_DIR/scripts/install_runtime.sh\""
echo ""
echo "You can launch it with:"
echo "  open \"$INSTALLED_APP_DIR\""
