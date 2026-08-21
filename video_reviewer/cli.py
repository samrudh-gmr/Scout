from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from video_reviewer.manifest import REVIEW_PENDING, write_manifest_csv
from video_reviewer.workflow import apply_manifest, build_prepare_manifest


def default_manifest_path() -> Path:
    return Path.home() / ".video-renamer" / "manifest.csv"


def command_prepare(args: argparse.Namespace) -> int:
    result = build_prepare_manifest(
        input_dir=Path(args.input).resolve(),
        year_month=args.year_month,
        client_or_location=args.client,
        start_seq=args.start_seq,
        tmp_dir=Path(args.tmp_dir).resolve(),
        proxy_scale=args.proxy_scale,
        sample_count=args.sample_count,
        ai_frame_max_width=args.ai_frame_max_width,
        ai_frame_quality=args.ai_frame_quality,
    )
    rows = result.rows
    manifest_path = Path(args.manifest).resolve()
    write_manifest_csv(manifest_path, rows)
    print(f"Wrote manifest: {manifest_path}")
    print(f"Files prepared: {len(rows)}")
    print(f"Rows pending review: {sum(1 for row in rows if row.review_status == REVIEW_PENDING)}")
    for message in result.skipped:
        print(f"Skipped (could not process): {message}")
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
        if action["status"] in {"would_rename", "would_copy"}:
            verb = "copy" if action["status"] == "would_copy" else "rename"
            print(f"Would {verb}: {action['source']} -> {action['target']}")
        elif action["status"] in {"renamed", "copied", "already_named"}:
            print(f"{action['status'].replace('_', ' ').title()}: {action['target']}")
    return 0 if result.ok else 1


def command_ai_review(args: argparse.Namespace) -> int:
    from video_reviewer.ai_review import (
        AiReviewError,
        ReviewPolicy,
        estimate_review_cost,
        pending_indices,
        review_rows_with_ai,
    )

    manifest_path = Path(args.manifest).resolve()
    if args.indices:
        indices = [int(i) for i in args.indices]
    else:
        indices = pending_indices(manifest_path)
    if not indices:
        print("No pending videos to review.")
        return 0
    policy = ReviewPolicy.from_preset(args.preset)
    estimate = estimate_review_cost(len(indices), policy)
    print(
        f"AI review plan: provider={args.provider}, preset={args.preset}, "
        f"videos={estimate['videos']}, frames/video≤{estimate['frames_per_video']}, "
        f"total frames≤{estimate['total_frames']} ({estimate['cost_tier']} cost tier)."
    )
    if args.dry_run:
        return 0

    def _progress(result) -> None:
        mark = "approved" if result.ok else result.status
        detail = result.error or result.proposed_name or result.response
        print(f"[{result.index}] {result.source_name}: {mark} — {detail}")

    try:
        results = review_rows_with_ai(
            manifest_path,
            indices,
            provider_id=args.provider,
            model=args.model or None,
            api_key=args.api_key or None,
            policy=policy,
            on_progress=_progress,
        )
    except AiReviewError as exc:
        print(str(exc))
        return 1

    approved = sum(1 for r in results if r.ok)
    print(f"Done: {approved}/{len(results)} approved.")
    return 0 if approved == len(results) else 1


def command_gemini_review(args: argparse.Namespace) -> int:
    args.provider = "gemini"
    return command_ai_review(args)


def command_gui(args: argparse.Namespace) -> int:
    from video_reviewer.gui import launch_gui

    manifest_path = Path(args.manifest).resolve() if args.manifest else default_manifest_path()
    launch_gui(
        manifest_path=manifest_path,
        host=args.host,
        port=args.port,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI-assisted GMR video reviewer and renamer.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Create proxies, sample frames, and an initial manifest.")
    prepare_parser.add_argument("--input", required=True, help="Folder containing source videos")
    prepare_parser.add_argument("--manifest", required=True, help="CSV manifest path to write")
    prepare_parser.add_argument(
        "--year-month",
        default="",
        help="Optional batch YYYY-MM value. If omitted, prepare falls back to filename patterns then embedded creation time.",
    )
    prepare_parser.add_argument("--client", default="", help="Optional client/location applied to every video in the folder")
    prepare_parser.add_argument("--start-seq", type=int, default=1, help="Starting sequence number")
    prepare_parser.add_argument("--tmp-dir", default="tmp", help="Directory for proxy and frame outputs")
    prepare_parser.add_argument("--proxy-scale", type=int, default=1280, help="Maximum proxy width")
    prepare_parser.add_argument("--sample-count", type=int, default=0, help="Number of sample frames to extract (default: auto-scaled by duration, ~1 per 30s, min 4, max 20)")
    prepare_parser.add_argument("--ai-frame-max-width", type=int, default=0, help="Optional max width for AI frames. Default 0 keeps source resolution.")
    prepare_parser.add_argument("--ai-frame-quality", type=int, default=2, help="JPEG quality for AI frames, 2 high quality to 31 low quality (default: 2)")
    prepare_parser.set_defaults(func=command_prepare)

    apply_parser = subparsers.add_parser("apply", help="Rename approved files from the manifest.")
    apply_parser.add_argument("--manifest", required=True, help="CSV manifest path to apply")
    apply_parser.add_argument("--output-dir", default="", help="Optional output folder instead of in-place rename")
    apply_parser.add_argument("--dry-run", action="store_true", help="Preview renames without changing files")
    apply_parser.set_defaults(func=command_apply)

    ai_parser = subparsers.add_parser(
        "ai-review",
        help="Classify pending videos with an API-key AI provider.",
    )
    ai_parser.add_argument("--manifest", required=True, help="CSV manifest path to review")
    ai_parser.add_argument("--provider", default="gemini", help="AI provider to use (default: gemini)")
    ai_parser.add_argument(
        "--indices",
        nargs="*",
        type=int,
        help="Specific row indices to review (default: all pending/blocked)",
    )
    ai_parser.add_argument("--model", default="", help="Optional provider model override")
    ai_parser.add_argument("--preset", choices=["fast", "balanced", "accurate"], default="balanced", help="Cost/accuracy preset")
    ai_parser.add_argument("--dry-run", action="store_true", help="Show cost/volume estimate without sending frames")
    ai_parser.add_argument(
        "--api-key",
        default="",
        help="Optional provider API key. If omitted, provider env vars are used (Gemini: GEMINI_API_KEY or GOOGLE_API_KEY).",
    )
    ai_parser.set_defaults(func=command_ai_review)

    gemini_parser = subparsers.add_parser(
        "gemini-review",
        help="Deprecated alias for: ai-review --provider gemini.",
    )
    gemini_parser.add_argument("--manifest", required=True, help="CSV manifest path to review")
    gemini_parser.add_argument("--indices", nargs="*", type=int, help="Specific row indices to review")
    gemini_parser.add_argument("--model", default="", help="Optional Gemini model override")
    gemini_parser.add_argument("--preset", choices=["fast", "balanced", "accurate"], default="balanced", help="Cost/accuracy preset")
    gemini_parser.add_argument("--dry-run", action="store_true", help="Show cost/volume estimate without sending frames")
    gemini_parser.add_argument("--api-key", default="", help="Optional Gemini API key or GEMINI_API_KEY env var.")
    gemini_parser.set_defaults(func=command_gemini_review)

    gui_parser = subparsers.add_parser("gui", help="Launch local web GUI for interactive review.")
    gui_parser.add_argument(
        "--manifest",
        default="",
        help="CSV manifest path to review. Optional for GUI; defaults to ~/.video-renamer/manifest.csv",
    )
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
    except (OSError, subprocess.CalledProcessError) as exc:
        # A missing manifest/input folder or a failed ffmpeg/ffprobe call should
        # print a clean one-line error, like every other expected failure here,
        # not dump a raw Python traceback.
        print(f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
