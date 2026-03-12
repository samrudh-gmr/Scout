from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path


MANIFEST_COLUMNS = [
    "source_path",
    "proxy_path",
    "sample_frames",
    "year_month",
    "description",
    "client_or_location",
    "sequence",
    "proposed_name",
    "ai_confidence",
    "ai_rationale",
    "ai_flags",
    "review_status",
    "capture_time",
    "source_hints",
]

REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_NEEDS_REVIEW = "needs_review"
REVIEW_BLOCKED = "blocked"
REVIEW_APPLIED = "applied"


@dataclass
class ManifestRow:
    source_path: str
    proxy_path: str = ""
    sample_frames: str = ""
    year_month: str = ""
    description: str = ""
    client_or_location: str = ""
    sequence: str = ""
    proposed_name: str = ""
    ai_confidence: str = ""
    ai_rationale: str = ""
    ai_flags: str = ""
    review_status: str = REVIEW_PENDING
    capture_time: str = ""
    source_hints: str = ""


def write_manifest_csv(path: Path, rows: list[ManifestRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def read_manifest_csv(path: Path) -> list[ManifestRow]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [ManifestRow(**row) for row in reader]
