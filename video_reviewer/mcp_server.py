"""MCP server for Claude Desktop integration with Video Renamer.

Exposes tools for reviewing, listing, and approving videos via Claude Desktop.

Usage:
    python -m video_reviewer.mcp_server --manifest /path/to/manifest.csv
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from video_reviewer.manifest import (
    manifest_transaction,
    REVIEW_APPROVED,
    REVIEW_BLOCKED,
    REVIEW_PENDING,
    ManifestRow,
    read_manifest_csv,
    write_manifest_csv,
)
from video_reviewer.workflow import resequence_and_rename

mcp = FastMCP("video-renamer")

_manifest_path: Path | None = None


def _get_manifest_path() -> Path:
    if _manifest_path is None:
        raise RuntimeError("Manifest path not configured. Pass --manifest when starting the server.")
    return _manifest_path


@mcp.tool()
def list_pending_videos() -> str:
    """List all pending or blocked videos from the manifest that need review.

    Returns a formatted table of videos with their current status, description,
    and client/location fields.
    """
    manifest = _get_manifest_path()
    rows = read_manifest_csv(manifest)
    pending = [
        (i, r) for i, r in enumerate(rows)
        if r.review_status in (REVIEW_PENDING, REVIEW_BLOCKED)
    ]
    if not pending:
        return "No pending or blocked videos found."

    lines = [f"Found {len(pending)} video(s) needing review:\n"]
    lines.append(f"{'#':<4} {'Status':<10} {'Source Name':<50} {'Description':<30} {'Client/Location'}")
    lines.append("-" * 120)
    for i, row in pending:
        source_name = Path(row.source_path).name
        lines.append(
            f"{i:<4} {row.review_status:<10} {source_name:<50} "
            f"{(row.description or '-'):<30} {row.client_or_location or '-'}"
        )
    return "\n".join(lines)


@mcp.tool()
def review_video(row_index: int) -> list[dict]:
    """Get frames and metadata for a specific video to review.

    Args:
        row_index: The index of the video row in the manifest (from list_pending_videos).

    Returns the video's sample frames as images and its metadata context so you can
    classify what process is shown and propose a filename.
    """
    manifest = _get_manifest_path()
    rows = read_manifest_csv(manifest)
    if row_index < 0 or row_index >= len(rows):
        return [{"type": "text", "text": f"Invalid row index: {row_index}. Use list_pending_videos to see valid indices."}]

    row = rows[row_index]
    source_name = Path(row.source_path).name

    # Build context info
    context = {
        "row_index": row_index,
        "source_name": source_name,
        "year_month": row.year_month,
        "sequence": row.sequence,
        "capture_time": row.capture_time,
        "current_status": row.review_status,
        "current_part": row.part or None,
        "current_description": row.description or None,
        "current_industry": row.industry or None,
        "current_client_or_location": row.client_or_location or None,
    }
    try:
        hints = json.loads(row.source_hints or "{}")
        context["source_hints"] = hints
    except json.JSONDecodeError:
        pass

    result: list[dict] = [
        {"type": "text", "text": f"## Video: {source_name}\n\n```json\n{json.dumps(context, indent=2)}\n```\n\nSample frames follow:"},
    ]

    # Add sample frames as images
    frame_paths = [f for f in (row.sample_frames or "").split("|") if f]
    if not frame_paths:
        result.append({"type": "text", "text": "No sample frames available for this video."})
        return result

    for idx, frame_str in enumerate(frame_paths):
        frame_path = Path(frame_str)
        # Mirrors gui.py's _safe_artifact(): the manifest is a plain,
        # locally-writable CSV, so a sample_frames cell isn't trustworthy on
        # its own — require an image suffix, a real (non-symlink) file, same
        # as the GUI's frame endpoint does for this same class of path.
        if (
            frame_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}
            or not frame_path.exists()
            or not frame_path.is_file()
            or frame_path.is_symlink()
        ):
            result.append({"type": "text", "text": f"Frame {idx + 1}: file not found ({frame_path.name})"})
            continue
        image_data = base64.standard_b64encode(frame_path.read_bytes()).decode("ascii")
        suffix = frame_path.suffix.lower()
        media_type = "image/png" if suffix == ".png" else "image/jpeg"
        result.append({
            "type": "image",
            "data": image_data,
            "mimeType": media_type,
        })

    result.append({
        "type": "text",
        "text": (
            "\nPlease analyze these frames and provide:\n"
            "1. **part**: Specific recognizable industrial part, or empty if none\n"
            "2. **description**: If part is present, provide only the process/action (e.g., 'Sanding'); otherwise use the established Action + Object phrase (e.g., 'Sanding Automotive Body Panel')\n"
            "3. **industry**: One approved industry category only when part is present; otherwise empty\n"
            "4. **client_or_location**: Client company or site name\n"
            "5. Whether this is a manual process (human operator visible)\n\n"
            "Then call `approve_video` with your classification."
        ),
    })
    return result


@mcp.tool()
def approve_video(
    row_index: int,
    description: str,
    client_or_location: str,
    year_month: str = "",
    industry: str = "",
    part: str = "",
) -> str:
    """Approve a video with the given classification and mark it as approved.

    Args:
        row_index: The index of the video row in the manifest.
        part: Optional specific recognizable part, or empty when no distinct part can be identified.
        description: Process/action when part is present (e.g., 'Sanding'); otherwise the established Action + Object description.
        client_or_location: The client company or site name.
        year_month: Optional YYYY-MM override. If empty, uses the existing value.

    Returns a confirmation message with the proposed filename.
    """
    manifest = _get_manifest_path()
    with manifest_transaction(manifest):
        rows = read_manifest_csv(manifest)
        if row_index < 0 or row_index >= len(rows):
            return f"Invalid row index: {row_index}"

        row = rows[row_index]
        source_name = Path(row.source_path).name

        row.description = description.strip()
        row.part = part.strip()
        row.industry = industry.strip()
        if not row.part:
            row.industry = ""
        row.client_or_location = client_or_location.strip()
        if year_month:
            row.year_month = year_month.strip()
        row.review_status = REVIEW_APPROVED

        # Without this, every row approved here keeps the "001" placeholder
        # sequence assigned at prepare time (when every description was still
        # blank), so two rows classified the same way collide on the same
        # proposed filename until apply() rejects the whole batch. Resequencing
        # can also renumber OTHER rows sharing this row's name group, so use
        # the shared helper that keeps every affected row's proposed_name in
        # sync, not just this one.
        errors = resequence_and_rename(rows)
        this_row_error = next((msg for msg in errors if msg.startswith(f"{source_name}:")), None)
        if this_row_error is not None:
            row.review_status = REVIEW_BLOCKED
            write_manifest_csv(manifest, rows)
            return f"Cannot approve: {this_row_error}"

        write_manifest_csv(manifest, rows)
    return (
        f"Approved: {source_name}\n"
        f"Proposed name: {row.proposed_name}\n"
        f"Description: {row.description}\n"
        f"Industry: {row.industry or 'none'}\n"
        f"Client/Location: {row.client_or_location}\n"
        f"Year-Month: {row.year_month}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for Video Renamer")
    parser.add_argument("--manifest", required=True, help="Path to the manifest CSV file")
    args = parser.parse_args()

    global _manifest_path
    _manifest_path = Path(args.manifest).resolve()

    if not _manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {_manifest_path}")

    mcp.run()


if __name__ == "__main__":
    main()
