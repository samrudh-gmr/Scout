from __future__ import annotations

import csv
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from filelock import FileLock


MANIFEST_LOCK = threading.RLock()


@contextmanager
def manifest_transaction(path: Path):
    """Serialize read-modify-write operations across GUI, CLI, and MCP processes."""
    lock_path = path.with_name(f".{path.name}.lock")
    with MANIFEST_LOCK, FileLock(lock_path, timeout=30):
        yield


MANIFEST_COLUMNS = [
    "source_path",
    "proxy_path",
    "sample_frames",
    "year_month",
    "part",
    "description",
    "industry",
    "client_or_location",
    "sequence",
    "proposed_name",
    "ai_confidence",
    "ai_rationale",
    "ai_flags",
    "ai_frame_metadata",
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
    part: str = ""
    description: str = ""
    industry: str = ""
    client_or_location: str = ""
    sequence: str = ""
    proposed_name: str = ""
    ai_confidence: str = ""
    ai_rationale: str = ""
    ai_flags: str = ""
    ai_frame_metadata: str = ""
    review_status: str = REVIEW_PENDING
    capture_time: str = ""
    source_hints: str = ""


def write_manifest_csv(path: Path, rows: list[ManifestRow]) -> None:
    with MANIFEST_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
                writer.writeheader()
                for row in rows:
                    writer.writerow(asdict(row))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)


def read_manifest_csv(path: Path) -> list[ManifestRow]:
    with MANIFEST_LOCK:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            allowed = set(MANIFEST_COLUMNS)
            return [ManifestRow(**{key: value for key, value in row.items() if key in allowed}) for row in reader]
