# 🐾 Scout

Production-focused video reviewer and renamer for the GMR SOP. It prepares video batches, extracts representative frames, supports manual review, Claude Desktop MCP review, and API-key AI review through a provider-neutral backend.

## Requirements

- Python 3.12+
- `ffmpeg` and `ffprobe` on `PATH` (`static-ffmpeg` can provide binaries on first use)
- `uv` recommended, or `pip`
- Optional: Claude Desktop for MCP review
- Optional: Gemini, OpenAI, or Anthropic API key for API-key AI review
- Optional: an official Codex CLI ChatGPT sign-in for local Codex-subscription review
- Native folder picker: Zenity on Linux, AppleScript on macOS, and PowerShell/.NET on Windows

## Installation

```bash
uv sync
# or
pip install .
```

## Recommended nontechnical workflow

```bash
uv run scout gui
```

Then open the local app and use the guided flow:

1. Click **Choose folder** and select the video folder; optionally set the date and client shared by that folder.
2. Click **Prepare videos** to create the manifest and sample frames.
3. Choose an API-key provider and paste its key, or choose **OpenAI Codex Subscription (local)** after completing its one-time sign-in.
4. Pick a cost/accuracy preset.
5. Review uncertain rows manually.
6. Preview renames, then apply.

The GUI defaults to `~/.video-renamer/manifest.csv`, so nontechnical users do not need to pass a manifest path. Advanced users can still launch a specific manifest with `uv run scout gui --manifest manifest.csv`. The legacy `video-renamer` command remains available for existing scripts.

## macOS app build

For a user who should not need Python, `uv`, or Terminal, build Scout as a native macOS application on a Mac:

```bash
./scripts/build_macos_app.sh
open dist/Scout.app
```

The resulting `Scout.app` opens the GUI in its own native window. The developer workflow above remains available, but an end user only needs to open the app. The first build requires the free `desktop` extra and PyInstaller; neither is needed by the end user. If macOS shows an unidentified-developer warning for an unsigned internal build, right-click the app and choose **Open** once.

The packaged app keeps the same local state and Codex setup as the CLI. A user may complete the one-time `codex login`, then use Scout without running `uv sync` or any other Terminal command during normal operation.

## Command quick reference

Run commands from the project directory with `uv run scout`:

```bash
# Launch the guided web interface
uv run scout gui

# Prepare videos and write a manifest
uv run scout prepare --input /path/to/videos --manifest manifest.csv

# Estimate AI review volume without sending frames
uv run scout ai-review --manifest manifest.csv --provider gemini --dry-run

# Review pending videos with an AI provider
uv run scout ai-review --manifest manifest.csv --provider gemini --preset balanced

# Preview approved renames
uv run scout apply --manifest manifest.csv --dry-run

# Apply approved renames in place
uv run scout apply --manifest manifest.csv
```

Use `uv run scout <command> --help` for all options. The old `video-renamer`
command remains available as a compatibility alias.

Folder selection is cross-platform. **Choose folder** uses the native desktop dialog when available. **Browse in app** is an always-available fallback that does not depend on Zenity, AppleScript, or PowerShell successfully opening a dialog.

## Prepare

Create proxies for local playback, extract source-quality representative frames for AI/manual review, and generate the initial manifest CSV:

```bash
uv run scout prepare --input /path/to/videos --manifest manifest.csv --client "SOLV California"
```

Options:

- `--year-month YYYY-MM` — batch override for the year-month field.
- `--client TEXT` — optional client/location applied to every video in the input folder.
- `--start-seq N` — retained for CLI compatibility; sequences are now derived from repeated final names.
- `--tmp-dir DIR` — directory for proxies and frames (default: `tmp`).
- `--proxy-scale N` — max proxy width in pixels for local playback (default: `1280`).
- `--sample-count N` — frames to extract per video. Default `0` auto-scales by duration: about one frame per 30 seconds, minimum 4, maximum 20.
- `--ai-frame-max-width N` — optional max width for AI frames. Default `0` keeps source resolution.
- `--ai-frame-quality N` — JPEG quality for AI frames, `2` high quality through `31` low quality (default: `2`).

Frame sampling is deterministic: frames are taken at midpoint timestamps across the video rather than from a low-res proxy or approximate FPS filter.

The sequence is automatic: a unique final name is always `001`; only otherwise-identical names increment (`001`, `002`, ...).

## API-key AI review

Supported providers:

| Provider | CLI value | Environment variable |
|---|---|---|
| Gemini | `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI Codex Subscription (local) | `codex-proxy` | Not applicable |

Provider-neutral command:

```bash
# Estimate frames/cost tier without sending anything
uv run scout ai-review --manifest manifest.csv --provider gemini --preset balanced --dry-run

# Review all pending/blocked rows
uv run scout ai-review --manifest manifest.csv --provider gemini --preset balanced

# Review specific rows
uv run scout ai-review --manifest manifest.csv --provider gemini --indices 0 3 5 \
  --model gemini-2.5-flash --api-key "$GEMINI_API_KEY"
```

`gemini-review` remains as a backwards-compatible alias for `ai-review --provider gemini`.

### Gemini setup

Create an API key at <https://aistudio.google.com/apikey>. Either paste it into the UI for a single run or export it:

```bash
export GEMINI_API_KEY="your-key"
# GOOGLE_API_KEY is also accepted
```

For OpenAI or Anthropic, select the provider in the UI and paste its key for the current run, or set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` before launch.

### Local Codex subscription setup

For a self-hosted deployment on an individual teammate's machine, select
**OpenAI Codex Subscription (local)** in the Name screen. `uv sync` installs
the local OpenAI-compatible proxy with Scout, and the app starts it
automatically on loopback (`127.0.0.1`) when this provider is selected. The app
also creates a private local request key automatically; it is never shown to
the browser or sent upstream.

The one manual prerequisite is the official Codex CLI sign-in on that same
machine:

```bash
codex login
```

This authenticates that teammate's ChatGPT subscription. Scout does
not read, copy, import, or refresh the Codex OAuth file; the bundled local
proxy owns that interaction. This provider is for private, per-device internal
use and depends on a community OpenAI-compatible proxy. Keep the ordinary
OpenAI API-key provider available for supported production/API use.

The app does not write pasted API keys to the manifest. External API review sends the selected rows' sampled frames plus metadata to the selected provider. For confidential footage, use an account/project whose data retention/training settings match your policy.

### Cost/accuracy presets

- `fast` — fewer frames and fewer retries; lowest cost.
- `balanced` — recommended default; good coverage without sending every possible frame.
- `accurate` — more frames while keeping a conservative auto-approval threshold.

Rows are auto-approved only when the AI response is confident, SOP-valid, and not using `Unknown` for client/location unless policy allows it. Otherwise they are marked `needs_review` for a human.

## Claude Desktop Review via MCP

The Claude MCP route is still supported. Configure Claude Desktop with the MCP server and use natural language to review videos.

```json
{
  "mcpServers": {
    "video-renamer": {
      "command": "uv",
      "args": [
        "run",
        "--project", "/path/to/claude_mcp",
        "python", "-m", "video_reviewer.mcp_server",
        "--manifest", "/path/to/manifest.csv"
      ]
    }
  }
}
```

Available MCP tools:

| Tool | Description |
|---|---|
| `list_pending_videos` | List all pending/blocked videos needing review |
| `review_video` | Get frames and metadata for a specific video |
| `approve_video` | Approve a video with classification data |

Example prompts:

- “List all pending videos that need review.”
- “Show me video 0 so I can classify it.”
- “This video shows manual sanding of an automotive body panel at SOLV California. Approve it.”

## Apply renames

```bash
# Preview first
uv run scout apply --manifest manifest.csv --dry-run

# Apply in place
uv run scout apply --manifest manifest.csv

# Or copy/rename into a separate output folder
uv run scout apply --manifest manifest.csv --output-dir /path/to/output
```

## Output filename format

```text
YYYY-MM_[Part]_[Process]_[IndustryCode]_[Client-Location]_###.ext
```

Example:

```text
2024-07_Recreational Vehicle Panel_Sanding_CR_SOLV California_002.mov
```

The review chat can discuss the open batch as well as the clip on screen. An explicit request such as “change the customer name in all videos to Acme” produces a visible all-clips proposal; it still must be reviewed and approved before the manifest changes.

## Development

```bash
uv sync --group dev
uv run pytest
```

Offline tests mock provider calls by default. Real provider smoke tests should be opt-in and require an explicit API key.
