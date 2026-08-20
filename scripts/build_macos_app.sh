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

# PyInstaller produces a self-contained .app. The app uses static-ffmpeg's
# existing first-run fallback if ffmpeg is not already present on the Mac.
uv run --extra desktop --with pyinstaller pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name Scout \
  --paths . \
  --add-data "video_reviewer/static:video_reviewer/static" \
  --collect-all webview \
  --collect-submodules video_reviewer \
  video_reviewer/desktop.py

printf '\nBuilt: %s\n' "$ROOT_DIR/dist/Scout.app"
printf 'Test it with: open dist/Scout.app\n'
