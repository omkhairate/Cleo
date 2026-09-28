#!/bin/zsh
set -euo pipefail

if [[ "$#" != "3" ]]; then
  echo "Usage: publish_app.sh <signed-app> <installed-app> <compatibility-path>" >&2
  exit 2
fi

SOURCE_APP="$1"
INSTALLED_APP="$2"
COMPATIBILITY_PATH="$3"
INSTALL_PARENT="$(dirname "$INSTALLED_APP")"
mkdir -p "$INSTALL_PARENT"
TEMP_INSTALL="$(mktemp -d "$INSTALL_PARENT/.cleo-install.XXXXXX")"
BACKUP_APP="$TEMP_INSTALL/previous.app"
NEW_APP="$TEMP_INSTALL/Cleo.app"

cleanup() {
  if [[ -e "$BACKUP_APP" && ! -e "$INSTALLED_APP" ]]; then
    mv "$BACKUP_APP" "$INSTALLED_APP"
  fi
  rm -rf "$TEMP_INSTALL"
}
trap cleanup EXIT

# Preserve signed contents without importing Finder metadata or resource forks.
ditto --norsrc --noextattr "$SOURCE_APP" "$NEW_APP"
xattr -dr com.apple.FinderInfo "$NEW_APP" 2>/dev/null || true
xattr -dr com.apple.ResourceFork "$NEW_APP" 2>/dev/null || true
if xattr -lr "$NEW_APP" | grep -Eq 'com\.apple\.(FinderInfo|ResourceFork):'; then
  echo "Finder metadata could not be removed from the app copy." >&2
  exit 1
fi
codesign --verify --strict "$NEW_APP"

# Do not leave a process using the previous executable and permission identity.
if [[ "${CLEO_STOP_RUNNING_APP:-1}" == "1" ]]; then
  CLEO_PROCESS_PATTERN='/Cleo( [0-9]+)?\.app/Contents/MacOS/CleoOverlay([[:space:]]|$)'
  pkill -TERM -f "$CLEO_PROCESS_PATTERN" 2>/dev/null || true
  for attempt in {1..50}; do
    if ! pgrep -f "$CLEO_PROCESS_PATTERN" >/dev/null; then
      break
    fi
    sleep 0.1
  done
  if pgrep -f "$CLEO_PROCESS_PATTERN" >/dev/null; then
    echo "A previous Cleo process is still running. Quit it before rebuilding." >&2
    exit 1
  fi
fi
if [[ -e "$INSTALLED_APP" || -L "$INSTALLED_APP" ]]; then
  mv "$INSTALLED_APP" "$BACKUP_APP"
fi
mv "$NEW_APP" "$INSTALLED_APP"
if ! codesign --verify --strict "$INSTALLED_APP"; then
  rm -rf "$INSTALLED_APP"
  exit 1
fi

mkdir -p "$(dirname "$COMPATIBILITY_PATH")"
if [[ -e "$COMPATIBILITY_PATH" || -L "$COMPATIBILITY_PATH" ]]; then
  rm -rf "$COMPATIBILITY_PATH"
fi
ln -s "$INSTALLED_APP" "$COMPATIBILITY_PATH"
