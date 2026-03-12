# Video Renamer — Claude MCP Edition

Interactive video reviewer and renamer for the GMR SOP. Uses Claude Desktop as the AI reviewer via the Model Context Protocol (MCP).

## Requirements

- Python 3.12+
- `ffmpeg` and `ffprobe` on `PATH`
- Claude Desktop (for AI-assisted review)
- `uv` (recommended) or `pip`

## Installation

```bash
uv sync
# or
pip install .
```

## Workflow

### 1. Prepare

Create proxies, extract sample frames, and generate the initial manifest CSV:

```bash
video-renamer prepare --input /path/to/videos --manifest manifest.csv
```

Options:
- `--year-month YYYY-MM` — batch override for the year-month field
- `--start-seq N` — starting sequence number (default: 1)
- `--tmp-dir DIR` — directory for proxies and frames (default: `tmp`)
- `--proxy-scale N` — max proxy width in pixels (default: 1280)
- `--sample-count N` — frames to extract per video (default: 4)

### 2. Claude Desktop Review (via MCP)

Configure Claude Desktop to connect to the MCP server, then use natural language to review videos.

#### MCP Configuration

Add the following to your Claude Desktop config:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**WSL:** Use `wsl.exe` as the command (see example below)

##### macOS / Windows (native Python)

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

##### Windows with WSL

```json
{
  "mcpServers": {
    "video-renamer": {
      "command": "wsl.exe",
      "args": [
        "/home/youruser/.local/bin/uv",
        "run",
        "--project", "/home/youruser/path/to/claude_mcp",
        "python", "-m", "video_reviewer.mcp_server",
        "--manifest", "/home/youruser/path/to/manifest.csv"
      ]
    }
  }
}
```

See `mcp_config_example.json` for a working example.

#### Example Prompts for Claude Desktop

Once configured, you can use these prompts in Claude Desktop:

- "List all pending videos that need review"
- "Show me video 0 so I can classify it"
- "This video shows manual sanding of an automotive body panel at SOLV California. Approve it."
- "Review all pending videos one by one"

#### Available MCP Tools

| Tool | Description |
|---|---|
| `list_pending_videos` | List all pending/blocked videos needing review |
| `review_video` | Get frames and metadata for a specific video |
| `approve_video` | Approve a video with classification data |

### 3. Apply

Rename approved files:

```bash
# Preview first
video-renamer apply --manifest manifest.csv --dry-run

# Apply renames
video-renamer apply --manifest manifest.csv
```

Options:
- `--output-dir DIR` — copy to a separate folder instead of renaming in-place
- `--dry-run` — preview without renaming

### Optional: Web GUI

Launch the web GUI for manual review without AI:

```bash
video-renamer gui --manifest manifest.csv
```

Options:
- `--host HOST` — bind address (default: `127.0.0.1`)
- `--port PORT` — bind port (default: `8765`)

## Output Filename Format

```
YYYY-MM_Description_Client-Location_###.ext
```

Example: `2024-07_Sanding Automotive Body Panel_SOLV California_002.mov`

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
uv run pytest
```
