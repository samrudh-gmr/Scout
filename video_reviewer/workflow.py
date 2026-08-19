from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from video_reviewer.manifest import (
    ManifestRow,
    REVIEW_APPLIED,
    REVIEW_APPROVED,
    REVIEW_BLOCKED,
    REVIEW_PENDING,
    manifest_transaction,
    read_manifest_csv,
    write_manifest_csv,
)
from video_reviewer.media import (
    compute_frame_count,
    create_frame_dir,
    create_proxy_path,
    extract_sample_frames,
    probe_media,
    require_fftools,
    run_ffmpeg_proxy,
    sample_timestamps,
)
from video_reviewer.sop import (
    VIDEO_EXTENSIONS,
    build_proposed_name,
    infer_source_hints,
    infer_year_month_from_name,
    natural_sort_key,
    validate_manifest_rows,
)


def resolve_year_month(batch_year_month: str, inferred_year_month: str, capture_time: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if batch_year_month:
        if inferred_year_month and inferred_year_month != batch_year_month:
            warnings.append(f"filename suggests {inferred_year_month} but batch uses {batch_year_month}")
        return batch_year_month, warnings
    if capture_time:
        warnings.append("year_month resolved from embedded creation_time")
        return capture_time[:7], warnings
    if inferred_year_month:
        warnings.append("year_month resolved from source filename pattern")
        return inferred_year_month, warnings
    warnings.append("year_month missing and needs manual review")
    return "", warnings


def build_prepare_manifest(
    *,
    input_dir: Path,
    year_month: str,
    client_or_location: str = "",
    start_seq: int,
    tmp_dir: Path,
    proxy_scale: int,
    sample_count: int,
    ai_frame_max_width: int = 0,
    ai_frame_quality: int = 2,
) -> list[ManifestRow]:
    require_fftools()
    batch_client_override = client_or_location.strip()
    candidates = sorted(
        [path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS],
        key=lambda item: natural_sort_key(item.name),
    )
    rows: list[ManifestRow] = []
    for path in candidates:
        metadata = probe_media(path)
        inferred_year_month, warnings = infer_year_month_from_name(path.name)
        resolved_year_month, resolution_warnings = resolve_year_month(
            year_month,
            inferred_year_month,
            metadata.get("capture_time", ""),
        )
        proxy_path = create_proxy_path(tmp_dir, path)
        frame_dir = create_frame_dir(tmp_dir, path)
        proxy_path.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg_proxy(path, proxy_path, proxy_scale)
        duration = float(metadata.get("duration") or 0)
        frame_count = compute_frame_count(duration) if sample_count == 0 else sample_count
        frame_paths = extract_sample_frames(
            path,
            frame_dir,
            frame_count,
            duration=duration,
            max_width=ai_frame_max_width,
            quality=ai_frame_quality,
        )
        frame_metadata = {
            frame.name: {
                "source": "original",
                "timestamp_seconds": sample_timestamps(duration, frame_count)[index] if frame_count else None,
                "downscaled": bool(ai_frame_max_width),
                "max_width": ai_frame_max_width,
                "quality": ai_frame_quality,
            }
            for index, frame in enumerate(frame_paths)
        }
        hint_flags = list(warnings)
        hint_flags.extend(resolution_warnings)
        rows.append(
            ManifestRow(
                source_path=str(path.resolve()),
                proxy_path=str(proxy_path.resolve()),
                sample_frames="|".join(str(frame.resolve()) for frame in frame_paths),
                year_month=resolved_year_month,
                client_or_location=batch_client_override,
                sequence="",
                review_status=REVIEW_PENDING,
                capture_time=metadata.get("capture_time", ""),
                source_hints=json.dumps(
                    {
                        "filename_inferred_year_month": inferred_year_month,
                        "warnings": hint_flags,
                        "filename_hints": infer_source_hints(path.name),
                        # A client chosen for the whole batch is operator input,
                        # not a model suggestion. Keep this provenance so AI
                        # review cannot replace it with a visual guess later.
                        "batch_client_override": batch_client_override,
                        "duration": metadata.get("duration", ""),
                        "size": metadata.get("size", ""),
                        "width": metadata.get("width", ""),
                        "height": metadata.get("height", ""),
                        "sample_frames": frame_metadata,
                    },
                    sort_keys=True,
                ),
                ai_frame_metadata=json.dumps(frame_metadata, sort_keys=True),
            )
        )
    rows.sort(key=lambda row: (row.capture_time == "", row.capture_time or "", natural_sort_key(Path(row.source_path).name)))
    assign_sequences_by_name(rows)
    return rows


def assign_sequences_by_name(rows: list[ManifestRow]) -> None:
    """Number only repeated complete names; a unique name is always ``001``."""
    seen: dict[tuple[str, str, str, str, str], int] = {}
    for row in rows:
        key = (
            row.year_month.strip(),
            row.part.strip(),
            row.description.strip(),
            row.industry.strip(),
            row.client_or_location.strip(),
        )
        if not row.year_month.strip() or not row.description.strip() or not row.client_or_location.strip():
            row.sequence = "001"
            continue
        seen[key] = seen.get(key, 0) + 1
        row.sequence = f"{seen[key]:03d}"


@dataclass
class ApplyResult:
    ok: bool
    actions: list[dict[str, str]]
    errors: list[str]


def apply_manifest(*, manifest_path: Path, output_dir: Path | None, dry_run: bool) -> ApplyResult:
    with manifest_transaction(manifest_path):
        rows = read_manifest_csv(manifest_path)
        errors = validate_manifest_rows(rows)
        plan: list[tuple[ManifestRow, Path, Path]] = []
        seen_targets: set[str] = set()
        for row in rows:
            if row.review_status != REVIEW_APPROVED:
                continue
            source = Path(row.source_path)
            target_name = build_proposed_name(row)
            target = (output_dir / target_name) if output_dir else source.with_name(target_name)
            target_key = str(target.absolute()).casefold()
            if target_key in seen_targets:
                errors.append(f"duplicate target path: {target}")
            seen_targets.add(target_key)
            if not source.exists():
                errors.append(f"Source does not exist: {source}")
            elif not source.is_file() or source.is_symlink():
                errors.append(f"Source must be a regular non-symlink file: {source}")
            if target.exists() and target.resolve() != source.resolve():
                errors.append(f"Target already exists: {target}")
            plan.append((row, source, target))
        if errors:
            return ApplyResult(ok=False, actions=[], errors=errors)

        preview_status = "would_copy" if output_dir else "would_rename"
        if dry_run:
            return ApplyResult(
                ok=True,
                actions=[{"source": source.name, "target": target.name, "status": preview_status} for _, source, target in plan],
                errors=[],
            )

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
        completed: list[tuple[Path, Path, bool]] = []
        actions: list[dict[str, str]] = []
        try:
            for row, source, target in plan:
                same_path = target.resolve() == source.resolve()
                if output_dir and not same_path:
                    shutil.copy2(source, target)
                    completed.append((source, target, True))
                    status = "copied"
                elif not same_path:
                    source.rename(target)
                    completed.append((source, target, False))
                    status = "renamed"
                else:
                    status = "already_named"
                row.source_path = str(target.resolve())
                row.proposed_name = target.name
                row.review_status = REVIEW_APPLIED
                actions.append({"source": source.name, "target": target.name, "status": status})
            write_manifest_csv(manifest_path, rows)
        except Exception as exc:  # best-effort rollback keeps batch state coherent
            rollback_errors: list[str] = []
            for source, target, was_copy in reversed(completed):
                try:
                    if was_copy:
                        target.unlink(missing_ok=True)
                    elif target.exists() and not source.exists():
                        target.rename(source)
                except Exception as rollback_exc:  # noqa: BLE001
                    rollback_errors.append(f"Rollback failed for {target.name}: {rollback_exc}")
            return ApplyResult(
                ok=False,
                actions=[],
                errors=[f"Apply failed before completion: {type(exc).__name__}: {exc}", *rollback_errors],
            )
        return ApplyResult(ok=True, actions=actions, errors=[])
