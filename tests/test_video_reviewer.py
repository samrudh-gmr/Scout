from __future__ import annotations

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
from video_reviewer.sop import build_proposed_name, infer_year_month_from_name, validate_field
from video_reviewer.workflow import apply_manifest, build_prepare_manifest


def write_fake_video(path: Path, size_bytes: int = 1024) -> None:
    path.write_bytes(b"0" * size_bytes)


def test_infer_year_month_from_grm_pattern() -> None:
    year_month, warnings = infer_year_month_from_name("24-GRM-0702-SOLV California 60FPS_V1-0002.mov")
    assert year_month == "2024-07"
    assert warnings


def test_validate_field_rejects_underscore() -> None:
    try:
        validate_field("description", "Bad_Value")
    except ValueError as exc:
        assert "underscores" in str(exc)
    else:
        raise AssertionError("Expected validate_field to reject underscores")


def test_prepare_creates_manifest_rows(monkeypatch, tmp_path: Path) -> None:
    a = tmp_path / "videos"
    a.mkdir()
    source = a / "24-GRM-0702-demo.mov"
    write_fake_video(source)

    monkeypatch.setattr("video_reviewer.workflow.require_fftools", lambda: None)
    monkeypatch.setattr(
        "video_reviewer.workflow.probe_media",
        lambda path: {"capture_time": "2024-07-02T10:00:00", "duration": "10", "size": "1024", "width": "1920", "height": "1080"},
    )
    monkeypatch.setattr("video_reviewer.workflow.run_ffmpeg_proxy", lambda source_path, proxy_path, scale: proxy_path.write_bytes(b"proxy"))

    def fake_extract(source_path: Path, frame_dir: Path, count: int, duration: float = 0.0) -> list[Path]:
        frame_dir.mkdir(parents=True, exist_ok=True)
        frames = []
        for index in range(count):
            frame = frame_dir / f"frame_{index:02d}.jpg"
            frame.write_bytes(b"frame")
            frames.append(frame)
        return frames

    monkeypatch.setattr("video_reviewer.workflow.extract_sample_frames", fake_extract)
    rows = build_prepare_manifest(
        input_dir=a,
        year_month="2024-07",
        start_seq=1,
        tmp_dir=tmp_path / "tmp",
        proxy_scale=1280,
        sample_count=3,
    )
    assert len(rows) == 1
    assert rows[0].review_status == REVIEW_PENDING
    assert rows[0].sequence == "001"
    assert rows[0].proxy_path.endswith(".proxy.mp4")
    assert rows[0].sample_frames.count("|") == 2


def test_prepare_without_batch_year_month_uses_filename_inference(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "videos"
    source_dir.mkdir()
    source = source_dir / "24-GRM-0702-demo.mov"
    write_fake_video(source)

    monkeypatch.setattr("video_reviewer.workflow.require_fftools", lambda: None)
    monkeypatch.setattr(
        "video_reviewer.workflow.probe_media",
        lambda path: {"capture_time": "", "duration": "10", "size": "1024", "width": "1920", "height": "1080"},
    )
    monkeypatch.setattr("video_reviewer.workflow.run_ffmpeg_proxy", lambda source_path, proxy_path, scale: proxy_path.write_bytes(b"proxy"))
    monkeypatch.setattr(
        "video_reviewer.workflow.extract_sample_frames",
        lambda source_path, frame_dir, count, duration=0.0: [],
    )

    rows = build_prepare_manifest(
        input_dir=source_dir,
        year_month="",
        start_seq=1,
        tmp_dir=tmp_path / "tmp",
        proxy_scale=1280,
        sample_count=3,
    )
    assert rows[0].year_month == "2024-07"
    assert "source filename pattern" in rows[0].source_hints


def test_prepare_without_batch_year_month_uses_creation_time(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "videos"
    source_dir.mkdir()
    source = source_dir / "demo.mov"
    write_fake_video(source)

    monkeypatch.setattr("video_reviewer.workflow.require_fftools", lambda: None)
    monkeypatch.setattr(
        "video_reviewer.workflow.probe_media",
        lambda path: {
            "capture_time": "2024-08-05T10:00:00",
            "duration": "10",
            "size": "1024",
            "width": "1920",
            "height": "1080",
        },
    )
    monkeypatch.setattr("video_reviewer.workflow.run_ffmpeg_proxy", lambda source_path, proxy_path, scale: proxy_path.write_bytes(b"proxy"))
    monkeypatch.setattr(
        "video_reviewer.workflow.extract_sample_frames",
        lambda source_path, frame_dir, count, duration=0.0: [],
    )

    rows = build_prepare_manifest(
        input_dir=source_dir,
        year_month="",
        start_seq=1,
        tmp_dir=tmp_path / "tmp",
        proxy_scale=1280,
        sample_count=3,
    )
    assert rows[0].year_month == "2024-08"
    assert "embedded creation_time" in rows[0].source_hints


def test_prepare_without_any_date_leaves_year_month_blank(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "videos"
    source_dir.mkdir()
    source = source_dir / "demo.mov"
    write_fake_video(source)

    monkeypatch.setattr("video_reviewer.workflow.require_fftools", lambda: None)
    monkeypatch.setattr(
        "video_reviewer.workflow.probe_media",
        lambda path: {"capture_time": "", "duration": "10", "size": "1024", "width": "1920", "height": "1080"},
    )
    monkeypatch.setattr("video_reviewer.workflow.run_ffmpeg_proxy", lambda source_path, proxy_path, scale: proxy_path.write_bytes(b"proxy"))
    monkeypatch.setattr(
        "video_reviewer.workflow.extract_sample_frames",
        lambda source_path, frame_dir, count, duration=0.0: [],
    )

    rows = build_prepare_manifest(
        input_dir=source_dir,
        year_month="",
        start_seq=1,
        tmp_dir=tmp_path / "tmp",
        proxy_scale=1280,
        sample_count=3,
    )
    assert rows[0].year_month == ""
    assert "needs manual review" in rows[0].source_hints


def test_apply_renames_only_approved(tmp_path: Path) -> None:
    approved = tmp_path / "approved.mov"
    blocked = tmp_path / "blocked.mov"
    write_fake_video(approved)
    write_fake_video(blocked)
    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(
        manifest,
        [
            ManifestRow(
                source_path=str(approved.resolve()),
                year_month="2024-07",
                description="Sanding Metal Panel",
                client_or_location="GMR HQ",
                sequence="001",
                proposed_name="2024-07_Sanding Metal Panel_GMR HQ_001.mov",
                review_status=REVIEW_APPROVED,
            ),
            ManifestRow(
                source_path=str(blocked.resolve()),
                year_month="2024-07",
                description="Grinding Metal Panel",
                client_or_location="GMR HQ",
                sequence="002",
                proposed_name="2024-07_Grinding Metal Panel_GMR HQ_002.mov",
                review_status=REVIEW_BLOCKED,
            ),
        ],
    )
    result = apply_manifest(manifest_path=manifest, output_dir=None, dry_run=False)
    assert result.ok
    assert not approved.exists()
    assert (tmp_path / "2024-07_Sanding Metal Panel_GMR HQ_001.mov").exists()
    assert blocked.exists()
    saved = read_manifest_csv(manifest)
    assert saved[0].review_status == REVIEW_APPLIED
    assert saved[1].review_status == REVIEW_BLOCKED


def test_build_proposed_name() -> None:
    row = ManifestRow(
        source_path="/tmp/demo.mov",
        year_month="2024-07",
        description="Sanding Metal Panel",
        client_or_location="GMR HQ",
        sequence="1",
    )
    assert build_proposed_name(row) == "2024-07_Sanding Metal Panel_GMR HQ_001.mov"
