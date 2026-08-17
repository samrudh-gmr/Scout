# Video Renamer

Production-focused video reviewer and renamer for the GMR SOP. It prepares video batches, extracts representative frames, supports manual review, Claude Desktop MCP review, and API-key AI review through a provider-neutral backend.

## Requirements

- Python 3.12+
- `ffmpeg` and `ffprobe` on `PATH` (`static-ffmpeg` can provide binaries on first use)
- `uv` recommended, or `pip`
- Optional: Claude Desktop for MCP review
- Optional: Gemini API key for API-key AI review

## Installation

```bash
uv sync
# or
pip install .
```

## Recommended nontechnical workflow

```bash
video-renamer prepare --input /path/to/videos --manifest manifest.csv
video-renamer gui --manifest manifest.csv
```

Then open the local app and use the guided flow:

1. Prepare videos / confirm rows.
2. Open **AI Review** at `http://127.0.0.1:8765/ai-review`.
3. Paste a provider API key or set the provider env var.
4. Pick a cost/accuracy preset.
5. Review uncertain rows manually.
6. Preview renames, then apply.

## Prepare

Create proxies for local playback, extract source-quality representative frames for AI/manual review, and generate the initial manifest CSV:

```bash
video-renamer prepare --input /path/to/videos --manifest manifest.csv
```

Options:

- `--year-month YYYY-MM` — batch override for the year-month field.
- `--start-seq N` — starting sequence number (default: `1`).
- `--tmp-dir DIR` — directory for proxies and frames (default: `tmp`).
- `--proxy-scale N` — max proxy width in pixels for local playback (default: `1280`).
- `--sample-count N` — frames to extract per video. Default `0` auto-scales by duration: about one frame per 30 seconds, minimum 4, maximum 20.
- `--ai-frame-max-width N` — optional max width for AI frames. Default `0` keeps source resolution.
- `--ai-frame-quality N` — JPEG quality for AI frames, `2` high quality through `31` low quality (default: `2`).

Frame sampling is deterministic: frames are taken at midpoint timestamps across the video rather than from a low-res proxy or approximate FPS filter.

## API-key AI review

Provider-neutral command:

```bash
# Estimate frames/cost tier without sending anything
video-renamer ai-review --manifest manifest.csv --provider gemini --preset balanced --dry-run

# Review all pending/blocked rows
video-renamer ai-review --manifest manifest.csv --provider gemini --preset balanced

# Review specific rows
video-renamer ai-review --manifest manifest.csv --provider gemini --indices 0 3 5 \
  --model gemini-2.5-flash --api-key "$GEMINI_API_KEY"
```

`gemini-review` remains as a backwards-compatible alias for `ai-review --provider gemini`.

### Gemini setup

Create an API key at <https://aistudio.google.com/apikey>. Either paste it into the UI for a single run or export it:

```bash
export GEMINI_API_KEY="your-key"
# GOOGLE_API_KEY is also accepted
```

The app does not write pasted API keys to the manifest. External API review sends the selected rows' sampled frames plus metadata to the selected provider. For confidential footage, use an account/project whose data retention/training settings match your policy.

### Cost/accuracy presets

- `fast` — fewer frames, no second-pass behavior; lowest cost.
- `balanced` — recommended default; good coverage without sending every possible frame.
- `accurate` — more frames and lower approval threshold for difficult batches.

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
video-renamer apply --manifest manifest.csv --dry-run

# Apply in place
video-renamer apply --manifest manifest.csv

# Or copy/rename into a separate output folder
video-renamer apply --manifest manifest.csv --output-dir /path/to/output
```

## Output filename format

```text
YYYY-MM_Description_Client-Location_###.ext
```

Example:

```text
2024-07_Sanding Automotive Body Panel_SOLV California_002.mov
```

## Development

```bash
uv sync --group dev
uv run pytest
```

Offline tests mock provider calls by default. Real provider smoke tests should be opt-in and require an explicit API key.
