#!/bin/zsh
set -euo pipefail

APP_BUNDLE_ID="ai.cleo.desktop"
APP_PATH="/Users/apollo/Desktop/Cleo/apps/desktop-macos/dist/Cleo.app"

echo "Quitting Cleo if it is running..."
pkill -f '/Cleo.app/Contents/MacOS/CleoOverlay' 2>/dev/null || true

echo "Resetting macOS permissions for $APP_BUNDLE_ID..."
tccutil reset Accessibility "$APP_BUNDLE_ID" 2>/dev/null || true
tccutil reset ScreenCapture "$APP_BUNDLE_ID" 2>/dev/null || true
tccutil reset Microphone "$APP_BUNDLE_ID" 2>/dev/null || true
tccutil reset SpeechRecognition "$APP_BUNDLE_ID" 2>/dev/null || true

echo ""
echo "Next:"
echo "  1. Open Cleo again:"
echo "     open \"$APP_PATH\""
echo "  2. When macOS prompts, allow Accessibility first."
echo "  3. Then allow Screen Recording if you want full app context."
echo "  4. Then allow Microphone and Speech Recognition if you want voice."
echo ""
echo "If macOS does not prompt automatically, open:"
echo "  System Settings > Privacy & Security > Accessibility"
echo "  System Settings > Privacy & Security > Screen Recording"
echo "  System Settings > Privacy & Security > Microphone"
echo "  System Settings > Privacy & Security > Speech Recognition"
