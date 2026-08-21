#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Scout macOS packaging must run on macOS (cross-building is not supported)." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Install the optional native-window dependency in the developer environment.
uv sync --extra desktop

# Generate a real macOS icon from the Scout paw mark. iconutil is included with
# Xcode Command Line Tools and turns the PNG iconset into the .icns PyInstaller
# expects.
ICONSET_DIR="$ROOT_DIR/build/Scout.iconset"
ICON_FILE="$ROOT_DIR/build/Scout.icns"
rm -rf "$ICONSET_DIR"
uv run --with Pillow python scripts/create_macos_icon.py "$ICONSET_DIR"
iconutil -c icns "$ICONSET_DIR" -o "$ICON_FILE"

# PyInstaller produces a self-contained .app. The local Codex proxy is a
# separate console entrypoint, so explicitly bundle its executable beside the
# Scout executable; the runtime looks for it next to sys.executable.
PROXY_BIN="$(command -v openai-api-server-via-codex || true)"
if [[ -z "$PROXY_BIN" || ! -x "$PROXY_BIN" ]]; then
  echo "The Codex proxy executable is missing. Run 'uv sync' and rebuild Scout." >&2
  exit 1
fi

uv run --extra desktop --with pyinstaller pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name Scout \
  --icon "$ICON_FILE" \
  --paths . \
  --add-data "video_reviewer/static:video_reviewer/static" \
  --add-binary "$PROXY_BIN:." \
  --collect-all webview \
  --collect-submodules video_reviewer \
  video_reviewer/desktop.py

printf '\nBuilt: %s\n' "$ROOT_DIR/dist/Scout.app"
printf 'Test it with: open dist/Scout.app\n'
