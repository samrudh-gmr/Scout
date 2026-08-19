from __future__ import annotations

import re
from pathlib import Path

from video_reviewer.manifest import REVIEW_APPLIED, REVIEW_APPROVED, ManifestRow


VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts", ".mxf", ".webm"}
INVALID_FILENAME_CHARS = set('/\\:*?"<>|')
INDUSTRY_CATEGORIES = (
    "Specialty Vehicle",
    "Marine & Boat Building",
    "General Manufacturing",
    "Consumer and Recreation",
    "Architecture",
    "Aerospace & Defense",
)


def natural_sort_key(value: str) -> list[object]:
    parts = re.split(r"(\d+)", value.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def clean_field(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def validate_field(name: str, value: str) -> str:
    value = clean_field(value)
    if not value:
        raise ValueError(f"{name} is required")
    if "_" in value:
        raise ValueError(f"{name} cannot contain underscores")
    if set(value) & INVALID_FILENAME_CHARS:
        raise ValueError(f"{name} contains forbidden filename characters")
    return value


def validate_year_month(value: str) -> str:
    value = clean_field(value)
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("year_month must use YYYY-MM")
    month = int(value.split("-")[1])
    if not 1 <= month <= 12:
        raise ValueError("year_month month must be between 01 and 12")
    return value


def validate_sequence(value: str) -> str:
    value = clean_field(value)
    if not value.isdigit():
        raise ValueError("sequence must be numeric")
    return f"{int(value):03d}"


def validate_industry(value: str) -> str:
    value = clean_field(value)
    if not value:
        return ""
    if value not in INDUSTRY_CATEGORIES:
        raise ValueError(f"industry must be one of: {', '.join(INDUSTRY_CATEGORIES)}")
    return value


def validate_optional_field(name: str, value: str) -> str:
    value = clean_field(value)
    if not value:
        return ""
    return validate_field(name, value)


def build_proposed_name(row: ManifestRow) -> str:
    ext = Path(row.source_path).suffix
    if row.industry and not row.part:
        raise ValueError("industry requires a part")
    return (
        f"{validate_year_month(row.year_month)}_"
        + (f"{validate_optional_field('part', row.part)}_" if row.part else "")
        + f"{validate_field('description', row.description)}_"
        + (f"{validate_industry(row.industry).replace(' ', '-').replace('&', 'and')}_" if row.industry else "")
        + f"{validate_field('client_or_location', row.client_or_location)}_"
        + f"{validate_sequence(row.sequence)}{ext}"
    )


def infer_year_month_from_name(name: str) -> tuple[str, list[str]]:
    matched = re.search(r"(?<!\d)(20\d{2})-(0[1-9]|1[0-2])_", name)
    if matched:
        return f"{matched.group(1)}-{matched.group(2)}", []

    matched = re.search(r"(?<!\d)(\d{2})-GRM-(\d{2})(\d{2})(?!\d)", name, re.IGNORECASE)
    if matched:
        year = 2000 + int(matched.group(1))
        month = int(matched.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}", ["year_month inferred from source filename pattern"]
    return "", ["year_month could not be inferred from source filename"]


def infer_source_hints(name: str) -> dict[str, str]:
    stem = Path(name).stem
    stem = re.sub(r"^copy of\s+", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^\d{2}-GRM-\d{4}-", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_V\d+-\d+$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s+", " ", stem).strip(" -_")
    return {"filename_tail": stem}


def validate_manifest_rows(rows: list[ManifestRow]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row.review_status not in {REVIEW_APPROVED, REVIEW_APPLIED}:
            continue
        try:
            proposed_name = build_proposed_name(row)
        except ValueError as exc:
            errors.append(f"{Path(row.source_path).name}: {exc}")
            continue
        if proposed_name in seen:
            errors.append(f"duplicate target filename: {proposed_name}")
        seen.add(proposed_name)
    return errors
