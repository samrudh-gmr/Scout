from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from video_reviewer.manifest import (
    ManifestRow,
    REVIEW_APPLIED,
    REVIEW_APPROVED,
    REVIEW_BLOCKED,
    REVIEW_PENDING,
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
    start_seq: int,
    tmp_dir: Path,
    proxy_scale: int,
    sample_count: int,
) -> list[ManifestRow]:
    require_fftools()
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
        frame_paths = extract_sample_frames(proxy_path, frame_dir, frame_count, duration=duration)
        hint_flags = list(warnings)
        hint_flags.extend(resolution_warnings)
        rows.append(
            ManifestRow(
                source_path=str(path.resolve()),
                proxy_path=str(proxy_path.resolve()),
                sample_frames="|".join(str(frame.resolve()) for frame in frame_paths),
                year_month=resolved_year_month,
                sequence="",
                review_status=REVIEW_PENDING,
                capture_time=metadata.get("capture_time", ""),
                source_hints=json.dumps(
                    {
                        "filename_inferred_year_month": inferred_year_month,
                        "warnings": hint_flags,
                        "filename_hints": infer_source_hints(path.name),
                        "duration": metadata.get("duration", ""),
                        "size": metadata.get("size", ""),
                        "width": metadata.get("width", ""),
                        "height": metadata.get("height", ""),
                    },
                    sort_keys=True,
                ),
            )
        )
    rows.sort(key=lambda row: (row.capture_time == "", row.capture_time or "", natural_sort_key(Path(row.source_path).name)))
    for index, row in enumerate(rows, start=start_seq):
        row.sequence = f"{index:03d}"
    return rows


@dataclass
class ApplyResult:
    ok: bool
    actions: list[dict[str, str]]
    errors: list[str]


def apply_manifest(*, manifest_path: Path, output_dir: Path | None, dry_run: bool) -> ApplyResult:
    rows = read_manifest_csv(manifest_path)
    errors = validate_manifest_rows(rows)
    if errors:
        return ApplyResult(ok=False, actions=[], errors=errors)
    actions: list[dict[str, str]] = []
    for row in rows:
        if row.review_status != REVIEW_APPROVED:
            continue
        target_name = build_proposed_name(row)
        target = Path(row.source_path).with_name(target_name)
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / target_name
        source = Path(row.source_path)
        if dry_run:
            actions.append({"source": source.name, "target": target.name, "status": "would_rename"})
            continue
        if target.exists() and target.resolve() != source.resolve():
            return ApplyResult(ok=False, actions=actions, errors=[f"Target already exists: {target}"])
        source.rename(target)
        row.source_path = str(target.resolve())
        row.proposed_name = target.name
        row.review_status = REVIEW_APPLIED
        actions.append({"source": source.name, "target": target.name, "status": "renamed"})
    write_manifest_csv(manifest_path, rows)
    return ApplyResult(ok=True, actions=actions, errors=[])
