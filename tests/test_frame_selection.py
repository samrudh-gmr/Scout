from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_reviewer.ai_review.models import ReviewPolicy
from video_reviewer.ai_review.service import _build_request
from video_reviewer.manifest import ManifestRow


@pytest.mark.parametrize("metadata_field", ["ai_frame_metadata", "source_hints"])
@pytest.mark.parametrize(
    ("count", "cap", "expected"),
    [
        (20, 12, [0, 2, 3, 5, 7, 9, 10, 12, 14, 16, 17, 19]),
        (12, 12, list(range(12))),
        (1, 12, [0]),
        (20, 1, [0]),
    ],
)
def test_request_frames_span_clip_and_keep_metadata_aligned(
    tmp_path: Path, metadata_field: str, count: int, cap: int, expected: list[int]
) -> None:
    paths = []
    metadata = {}
    for index in range(count):
        path = tmp_path / f"frame_{index:02d}.jpg"
        path.write_bytes(f"frame {index}".encode())
        paths.append(path)
        metadata[path.name] = {
            "timestamp_seconds": index * 100.0,
            "width": 640 + index,
            "height": 360 + index,
            "downscaled": bool(index % 2),
        }
    row = ManifestRow(
        source_path=str(tmp_path / "clip.mov"),
        sample_frames="|".join(str(path) for path in paths),
    )
    payload = metadata if metadata_field == "ai_frame_metadata" else {"sample_frames": metadata}
    setattr(row, metadata_field, json.dumps(payload))

    request = _build_request(row, ReviewPolicy(max_frames=cap))

    assert [frame.path for frame in request.frames] == [paths[index] for index in expected]
    assert [frame.data for frame in request.frames] == [f"frame {index}".encode() for index in expected]
    assert [frame.timestamp_seconds for frame in request.frames] == [index * 100.0 for index in expected]
    assert [frame.width for frame in request.frames] == [640 + index for index in expected]
    assert [frame.height for frame in request.frames] == [360 + index for index in expected]
    assert [frame.downscaled for frame in request.frames] == [bool(index % 2) for index in expected]
