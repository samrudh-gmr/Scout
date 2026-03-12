from __future__ import annotations

import argparse
from pathlib import Path

from video_reviewer.manifest import REVIEW_PENDING, write_manifest_csv
from video_reviewer.workflow import apply_manifest, build_prepare_manifest


def command_prepare(args: argparse.Namespace) -> int:
    rows = build_prepare_manifest(
        input_dir=Path(args.input).resolve(),
        year_month=args.year_month,
        start_seq=args.start_seq,
        tmp_dir=Path(args.tmp_dir).resolve(),
        proxy_scale=args.proxy_scale,
        sample_count=args.sample_count,
    )
    manifest_path = Path(args.manifest).resolve()
    write_manifest_csv(manifest_path, rows)
    print(f"Wrote manifest: {manifest_path}")
    print(f"Files prepared: {len(rows)}")
    print(f"Rows pending review: {sum(1 for row in rows if row.review_status == REVIEW_PENDING)}")
    return 0


def command_apply(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    result = apply_manifest(
        manifest_path=Path(args.manifest).resolve(),
        output_dir=output_dir,
        dry_run=args.dry_run,
    )
    for error in result.errors:
        print(error)
    for action in result.actions:
        if action["status"] == "would_rename":
            print(f"Would rename: {action['source']} -> {action['target']}")
        elif action["status"] == "renamed":
            print(f"Renamed: {action['target']}")
    return 0 if result.ok else 1


def command_gui(args: argparse.Namespace) -> int:
    from video_reviewer.gui import launch_gui

    launch_gui(
        manifest_path=Path(args.manifest).resolve(),
        host=args.host,
        port=args.port,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-assisted GMR video reviewer and renamer (Claude MCP edition).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Create proxies, sample frames, and an initial manifest.")
    prepare_parser.add_argument("--input", required=True, help="Folder containing source videos")
    prepare_parser.add_argument("--manifest", required=True, help="CSV manifest path to write")
    prepare_parser.add_argument(
        "--year-month",
        default="",
        help="Optional batch YYYY-MM value. If omitted, prepare falls back to filename patterns then embedded creation time.",
    )
    prepare_parser.add_argument("--start-seq", type=int, default=1, help="Starting sequence number")
    prepare_parser.add_argument("--tmp-dir", default="tmp", help="Directory for proxy and frame outputs")
    prepare_parser.add_argument("--proxy-scale", type=int, default=1280, help="Maximum proxy width")
    prepare_parser.add_argument("--sample-count", type=int, default=0, help="Number of sample frames to extract (default: auto-scaled by duration, ~1 per 30s, min 4, max 20)")
    prepare_parser.set_defaults(func=command_prepare)

    apply_parser = subparsers.add_parser("apply", help="Rename approved files from the manifest.")
    apply_parser.add_argument("--manifest", required=True, help="CSV manifest path to apply")
    apply_parser.add_argument("--output-dir", default="", help="Optional output folder instead of in-place rename")
    apply_parser.add_argument("--dry-run", action="store_true", help="Preview renames without changing files")
    apply_parser.set_defaults(func=command_apply)

    gui_parser = subparsers.add_parser("gui", help="Launch local web GUI for interactive review.")
    gui_parser.add_argument("--manifest", required=True, help="CSV manifest path to review")
    gui_parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    gui_parser.add_argument("--port", type=int, default=8765, help="Port to bind")
    gui_parser.set_defaults(func=command_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
