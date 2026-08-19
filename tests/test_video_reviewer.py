from __future__ import annotations

from pathlib import Path

from video_reviewer.manifest import (
    ManifestRow,
    REVIEW_APPLIED,
    REVIEW_APPROVED,
    REVIEW_BLOCKED,
    REVIEW_NEEDS_REVIEW,
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

    def fake_extract(source_path: Path, frame_dir: Path, count: int, duration: float = 0.0, **kwargs) -> list[Path]:
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


def test_prepare_applies_optional_folder_client(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "videos"
    source_dir.mkdir()
    write_fake_video(source_dir / "one.mov")
    write_fake_video(source_dir / "two.mov")
    monkeypatch.setattr("video_reviewer.workflow.require_fftools", lambda: None)
    monkeypatch.setattr("video_reviewer.workflow.probe_media", lambda path: {"capture_time": "", "duration": "1"})
    monkeypatch.setattr("video_reviewer.workflow.run_ffmpeg_proxy", lambda source, proxy, scale: proxy.write_bytes(b"proxy"))
    monkeypatch.setattr("video_reviewer.workflow.extract_sample_frames", lambda *args, **kwargs: [])

    rows = build_prepare_manifest(
        input_dir=source_dir, year_month="2024-07", client_or_location="SOLV California",
        start_seq=1, tmp_dir=tmp_path / "tmp", proxy_scale=1280, sample_count=1,
    )

    assert [row.client_or_location for row in rows] == ["SOLV California", "SOLV California"]


def test_sequences_only_increment_for_repeated_complete_names() -> None:
    from video_reviewer.workflow import assign_sequences_by_name

    rows = [
        ManifestRow(source_path="one.mov", year_month="2024-07", description="Sanding Panel", client_or_location="Acme"),
        ManifestRow(source_path="two.mov", year_month="2024-07", description="Cleaning Panel", client_or_location="Acme"),
        ManifestRow(source_path="three.mov", year_month="2024-07", description="Sanding Panel", client_or_location="Acme"),
    ]
    assign_sequences_by_name(rows)

    assert [row.sequence for row in rows] == ["001", "001", "002"]


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
        lambda source_path, frame_dir, count, duration=0.0, **kwargs: [],
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
        lambda source_path, frame_dir, count, duration=0.0, **kwargs: [],
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
        lambda source_path, frame_dir, count, duration=0.0, **kwargs: [],
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


def test_sample_timestamps_use_midpoints() -> None:
    from video_reviewer.media import sample_timestamps

    assert sample_timestamps(100.0, 4) == [12.5, 37.5, 62.5, 87.5]


def test_compute_frame_count_ceil_min_and_cap() -> None:
    from video_reviewer.media import compute_frame_count

    assert compute_frame_count(10) == 4
    assert compute_frame_count(121) == 5
    assert compute_frame_count(9999) == 20


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


def _manifest_with_frame(tmp_path: Path) -> Path:
    """A one-row pending manifest whose single sample frame exists on disk."""
    frame = tmp_path / "frame_00.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")  # minimal JPEG-ish bytes
    source = tmp_path / "clip.mov"
    source.write_bytes(b"0")
    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(
        manifest,
        [
            ManifestRow(
                source_path=str(source.resolve()),
                sample_frames=str(frame.resolve()),
                year_month="2024-07",
                sequence="001",
                review_status=REVIEW_PENDING,
            )
        ],
    )
    return manifest


def test_gemini_available_reports_missing_sdk(monkeypatch) -> None:
    from video_reviewer import gemini_review

    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: False)
    sdk_ready, has_key, message = gemini_review.gemini_available()
    assert sdk_ready is False and has_key is False
    assert "google-genai" in message


def test_gemini_available_needs_key(monkeypatch) -> None:
    from video_reviewer import gemini_review

    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    sdk_ready, has_key, message = gemini_review.gemini_available()
    assert sdk_ready is True and has_key is False
    assert "aistudio.google.com" in message


def test_pending_indices_includes_pending_and_blocked(tmp_path: Path) -> None:
    from video_reviewer.gemini_review import pending_indices

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(
        manifest,
        [
            ManifestRow(source_path="/tmp/a.mov", review_status=REVIEW_PENDING),
            ManifestRow(source_path="/tmp/b.mov", review_status=REVIEW_APPROVED),
            ManifestRow(source_path="/tmp/c.mov", review_status=REVIEW_BLOCKED),
        ],
    )
    assert pending_indices(manifest) == [0, 2]


def test_review_rows_approves_high_confidence(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import gemini_review

    manifest = _manifest_with_frame(tmp_path)
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.setattr(
        "video_reviewer.ai_review.providers.gemini.GeminiProvider._generate",
        lambda self, key, model, prompt, frames: {
            "description": "Sanding Metal Panel",
            "client_or_location": "GMR HQ",
            "is_manual": True,
            "confidence": 0.92,
            "rationale": "Operator sanding a panel.",
            "flags": [],
        },
    )

    results = gemini_review.review_rows(manifest, [0], api_key="test-key")
    result = results[0]
    assert result.ok is True
    assert result.status == REVIEW_APPROVED
    # is_manual is true, so the SOP's Manual prefix must be applied.
    assert result.proposed_name == "2024-07_Manual Sanding Metal Panel_GMR HQ_001.mov"
    assert result.confidence == 0.92

    saved = read_manifest_csv(manifest)
    assert saved[0].ai_confidence == "0.92"
    assert saved[0].ai_rationale == "Operator sanding a panel."


def test_review_rows_flags_low_confidence_for_review(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import gemini_review

    manifest = _manifest_with_frame(tmp_path)
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.setattr(
        "video_reviewer.ai_review.providers.gemini.GeminiProvider._generate",
        lambda self, key, model, prompt, frames: {
            "description": "Sanding Metal Panel",
            "client_or_location": "Unknown",
            "confidence": 0.30,
            "rationale": "Frames are blurry.",
            "flags": ["low light"],
        },
    )

    result = gemini_review.review_rows(manifest, [0], api_key="test-key")[0]
    assert result.ok is False
    assert result.status == REVIEW_NEEDS_REVIEW
    assert not result.error


def test_review_rows_blocks_invalid_field(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import gemini_review

    manifest = _manifest_with_frame(tmp_path)
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.setattr(
        "video_reviewer.ai_review.providers.gemini.GeminiProvider._generate",
        lambda self, key, model, prompt, frames: {
            "description": "Bad_Description",  # underscore violates the SOP
            "client_or_location": "GMR HQ",
            "confidence": 0.95,
            "rationale": "x",
            "flags": [],
        },
    )

    result = gemini_review.review_rows(manifest, [0], api_key="test-key")[0]
    assert result.ok is False
    assert result.status == REVIEW_BLOCKED
    assert "underscore" in result.error


def test_review_rows_surfaces_api_error(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import gemini_review

    manifest = _manifest_with_frame(tmp_path)
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)

    def boom(self, key, model, prompt, frames):
        raise RuntimeError("429 rate limit")

    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._generate", boom)
    result = gemini_review.review_rows(manifest, [0], api_key="test-key")[0]
    assert result.ok is False
    assert "rate limit" in result.error


def test_review_rows_requires_api_key(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import gemini_review

    manifest = _manifest_with_frame(tmp_path)
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    try:
        gemini_review.review_rows(manifest, [0])
    except gemini_review.GeminiError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("Expected GeminiError when no API key is available")


def test_choose_port_skips_busy_port() -> None:
    import socket

    from video_reviewer.gui import _choose_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy_port = sock.getsockname()[1]
        assert _choose_port("127.0.0.1", busy_port, attempts=2) == busy_port + 1


def test_build_proposed_name() -> None:
    from video_reviewer.sop import VIDEO_EXTENSIONS

    assert ".webm" in VIDEO_EXTENSIONS
    row = ManifestRow(
        source_path="/tmp/demo.mov",
        year_month="2024-07",
        description="Sanding Metal Panel",
        client_or_location="GMR HQ",
        sequence="1",
    )
    assert build_proposed_name(row) == "2024-07_Sanding Metal Panel_GMR HQ_001.mov"


def test_ai_providers_are_registered_and_report_provider_specific_keys(monkeypatch) -> None:
    from video_reviewer.ai_review import available_providers, provider_status

    providers = {item["id"]: item for item in available_providers()}
    assert {"gemini", "openai", "anthropic"} <= providers.keys()
    assert providers["openai"]["env_key_names"] == ["OPENAI_API_KEY"]
    assert providers["anthropic"]["env_key_names"] == ["ANTHROPIC_API_KEY"]

    monkeypatch.setattr("video_reviewer.ai_review.providers.openai.OpenAIProvider._sdk_installed", lambda self: True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status = provider_status("openai")
    assert status.available and not status.has_key
    assert "OPENAI_API_KEY" in status.message


def test_pick_dir_returns_actionable_missing_zenity_error(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer.gui import _pick_directory_sync

    monkeypatch.setattr("video_reviewer.gui.shutil.which", lambda name: None)
    try:
        _pick_directory_sync()
    except FileNotFoundError as exc:
        assert "zenity" in str(exc)
        assert "paste" in str(exc)
    else:
        raise AssertionError("Expected a clear missing-zenity error")


def test_pick_dir_distinguishes_cancel(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer.gui import _pick_directory_sync

    class Completed:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr("video_reviewer.gui.shutil.which", lambda name: "/usr/bin/zenity")
    monkeypatch.setattr("video_reviewer.gui.subprocess.run", lambda *args, **kwargs: Completed())
    result = _pick_directory_sync()
    assert result.returncode == 1
    assert result.stdout == ""



def test_apply_preflight_collision_causes_zero_mutations(tmp_path: Path) -> None:
    first = tmp_path / "first.mov"
    second = tmp_path / "second.mov"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    manifest = tmp_path / "manifest.csv"
    rows = [
        ManifestRow(
            source_path=str(first), year_month="2024-07", description="Sanding Panel",
            client_or_location="GMR HQ", sequence="001", review_status=REVIEW_APPROVED,
        ),
        ManifestRow(
            source_path=str(second), year_month="2024-07", description="Welding Frame",
            client_or_location="GMR HQ", sequence="002", review_status=REVIEW_APPROVED,
        ),
    ]
    write_manifest_csv(manifest, rows)
    collision = tmp_path / "2024-07_Welding Frame_GMR HQ_002.mov"
    collision.write_bytes(b"existing")

    result = apply_manifest(manifest_path=manifest, output_dir=None, dry_run=False)

    assert result.ok is False
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert collision.read_bytes() == b"existing"
    saved = read_manifest_csv(manifest)
    assert [row.review_status for row in saved] == [REVIEW_APPROVED, REVIEW_APPROVED]


def test_apply_output_dir_copies_without_removing_source(tmp_path: Path) -> None:
    source = tmp_path / "source.mov"
    source.write_bytes(b"video")
    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [ManifestRow(
        source_path=str(source), year_month="2024-07", description="Sanding Panel",
        client_or_location="GMR HQ", sequence="001", review_status=REVIEW_APPROVED,
    )])
    output = tmp_path / "output"

    result = apply_manifest(manifest_path=manifest, output_dir=output, dry_run=False)

    assert result.ok
    assert source.read_bytes() == b"video"
    assert (output / "2024-07_Sanding Panel_GMR HQ_001.mov").read_bytes() == b"video"
    assert result.actions[0]["status"] == "copied"


def test_artifact_paths_are_unique_for_same_stem(tmp_path: Path) -> None:
    from video_reviewer.media import create_frame_dir, create_proxy_path

    mov = tmp_path / "clip.mov"
    mp4 = tmp_path / "clip.mp4"
    assert create_proxy_path(tmp_path, mov) != create_proxy_path(tmp_path, mp4)
    assert create_frame_dir(tmp_path, mov) != create_frame_dir(tmp_path, mp4)


def test_ai_review_rejects_explicit_empty_and_malformed_indices(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [])
    client = TestClient(create_app(manifest))
    assert client.post("/api/ai/review", json={"indices": []}).status_code == 422
    assert client.post("/api/ai/review", json={"indices": ["bad"]}).status_code == 422


def test_video_endpoint_rejects_empty_proxy_path(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [ManifestRow(source_path=str(tmp_path / "source.mov"), proxy_path="")])
    response = TestClient(create_app(manifest)).get("/api/video/0")
    assert response.status_code == 404
    assert response.json()["error"] == "proxy not found"


def test_gui_rejects_non_loopback_host(tmp_path: Path) -> None:
    from video_reviewer.gui import launch_gui

    try:
        launch_gui(tmp_path / "manifest.csv", "0.0.0.0", 8765)
    except RuntimeError as exc:
        assert "localhost" in str(exc)
    else:
        raise AssertionError("Expected non-loopback host to be rejected")



def test_prepare_empty_folder_keeps_existing_manifest(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    existing = ManifestRow(source_path=str(tmp_path / "existing.mov"), review_status=REVIEW_PENDING)
    write_manifest_csv(manifest, [existing])
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr("video_reviewer.workflow.build_prepare_manifest", lambda **kwargs: [])

    response = TestClient(create_app(manifest)).post(
        "/api/run-prepare",
        json={"input_dir": str(empty), "tmp_dir": str(tmp_path / "cache")},
    )

    assert response.status_code == 422
    assert read_manifest_csv(manifest)[0].source_path == existing.source_path


def test_pick_dir_api_returns_selected_folder(monkeypatch, tmp_path: Path) -> None:
    import subprocess
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [])
    selected = tmp_path / "selected"
    selected.mkdir()
    completed = subprocess.CompletedProcess(["zenity"], 0, stdout=f"{selected}\n", stderr="")
    monkeypatch.setattr("video_reviewer.gui._pick_directory_sync", lambda: completed)

    response = TestClient(create_app(manifest)).get("/api/pick-dir")

    assert response.status_code == 200
    assert response.json() == {"path": str(selected), "cancelled": False}


def test_ai_result_is_not_applied_after_manifest_row_changes(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer.ai_review.models import ProviderConfig, ReviewPolicy, ReviewResponse
    from video_reviewer.ai_review.service import _review_one_row

    manifest = _manifest_with_frame(tmp_path)

    class Provider:
        def classify(self, request, config):
            replacement = ManifestRow(
                source_path=str(tmp_path / "different.mov"),
                sample_frames=read_manifest_csv(manifest)[0].sample_frames,
                year_month="2024-07",
                sequence="001",
                review_status=REVIEW_PENDING,
            )
            write_manifest_csv(manifest, [replacement])
            return ReviewResponse(
                description="Sanding Panel", client_or_location="GMR HQ",
                is_manual=True, confidence=0.95,
            )

    result = _review_one_row(
        manifest, 0, Provider(), ProviderConfig(provider_id="fake", api_key="x"), ReviewPolicy()
    )

    assert result.ok is False
    assert "video list changed" in result.error.lower()
    saved = read_manifest_csv(manifest)[0]
    assert saved.description == ""
    assert saved.review_status == REVIEW_PENDING


def test_string_false_is_not_parsed_as_manual() -> None:
    from video_reviewer.ai_review.providers.common import parse_response

    parsed = parse_response({
        "description": "Sanding Panel", "client_or_location": "GMR HQ",
        "is_manual": "false", "confidence": 0.8,
    })
    assert parsed.is_manual is False



def test_pick_dir_preserves_filesystem_root(monkeypatch, tmp_path: Path) -> None:
    import subprocess
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [])
    monkeypatch.setattr(
        "video_reviewer.gui._pick_directory_sync",
        lambda: subprocess.CompletedProcess(["zenity"], 0, stdout="/\n", stderr=""),
    )
    response = TestClient(create_app(manifest)).get("/api/pick-dir")
    assert response.json() == {"path": "/", "cancelled": False}


def test_unexpected_ai_error_does_not_leak_exception_text(monkeypatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [ManifestRow(source_path=str(tmp_path / "a.mov"))])

    def fail(*args, **kwargs):
        raise RuntimeError("secret-key-should-not-leak")

    monkeypatch.setattr("video_reviewer.ai_review.review_rows_with_ai", fail)
    response = TestClient(create_app(manifest)).post(
        "/api/ai/review", json={"indices": [0], "provider": "gemini", "api_key": "x"}
    )
    assert response.status_code == 500
    assert "secret-key-should-not-leak" not in response.text



def test_accurate_preset_keeps_conservative_approval_threshold() -> None:
    from video_reviewer.ai_review.models import ReviewPolicy

    assert ReviewPolicy.from_preset("accurate").confidence_threshold >= ReviewPolicy.from_preset("balanced").confidence_threshold



def test_native_picker_sanitizes_snap_loader_environment(monkeypatch) -> None:
    from video_reviewer.gui import _pick_directory_sync

    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        class Completed:
            returncode = 1
            stdout = ""
            stderr = ""
        return Completed()

    monkeypatch.setattr("video_reviewer.gui.sys.platform", "linux")
    monkeypatch.setattr("video_reviewer.gui.shutil.which", lambda name: "/usr/bin/zenity")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/snap/core20/current/lib/x86_64-linux-gnu")
    monkeypatch.setenv("GIO_EXTRA_MODULES", "/snap/core20/modules")
    monkeypatch.setattr("video_reviewer.gui.subprocess.run", fake_run)

    _pick_directory_sync()

    assert "LD_LIBRARY_PATH" not in captured["env"]
    assert "GIO_EXTRA_MODULES" not in captured["env"]
    assert captured["timeout"] == 20


def test_in_app_folder_browser_lists_directories_cross_platform(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    (tmp_path / "Videos").mkdir()
    (tmp_path / "Other").mkdir()
    (tmp_path / ".hidden").mkdir()
    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [])

    response = TestClient(create_app(manifest)).get("/api/browse-dir", params={"path": str(tmp_path)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == str(tmp_path.resolve())
    assert [item["name"] for item in payload["dirs"]] == ["Other", "Videos"]
    assert all(Path(item["path"]).is_absolute() for item in payload["dirs"])


def test_in_app_folder_browser_rejects_missing_path(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [])
    response = TestClient(create_app(manifest)).get(
        "/api/browse-dir", params={"path": str(tmp_path / "missing")}
    )
    assert response.status_code == 404



def test_native_picker_uses_platform_commands(monkeypatch) -> None:
    from video_reviewer.gui import _pick_directory_sync

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        class Completed:
            returncode = 1
            stdout = ""
            stderr = ""
        return Completed()

    monkeypatch.setattr("video_reviewer.gui.subprocess.run", fake_run)

    monkeypatch.setattr("video_reviewer.gui.sys.platform", "darwin")
    _pick_directory_sync()
    assert commands[-1][0] == "osascript"

    monkeypatch.setattr("video_reviewer.gui.sys.platform", "win32")
    _pick_directory_sync()
    assert commands[-1][:3] == ["powershell", "-NoProfile", "-Command"]


def test_save_derives_sequence_from_repeated_names(tmp_path: Path) -> None:
    """A unique name is 001 regardless of its position in the import batch."""
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [ManifestRow(source_path=str(tmp_path / "clip.mov"))])
    client = TestClient(create_app(manifest))

    response = client.post("/api/save", json={"rows": [{
        "index": 0,
        "checked": True,
        "description": "Robotic Sanding Composite Panel",
        "client_or_location": "SOLV California",
        "year_month": "2024-07",
        "sequence": "3",
    }]})

    assert response.status_code == 200, response.json()
    row = read_manifest_csv(manifest)[0]
    assert row.review_status == REVIEW_APPROVED
    assert row.proposed_name == "2024-07_Robotic Sanding Composite Panel_SOLV California_001.mov"


def test_api_key_is_stored_privately_and_never_returned(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from video_reviewer import config
    from video_reviewer.gui import create_app

    monkeypatch.setattr(config, "_CONFIG_DIR", tmp_path / "home")
    monkeypatch.setattr(config, "_KEYS_FILE", tmp_path / "home" / "keys.json")

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [])
    client = TestClient(create_app(manifest))

    assert client.post("/api/settings/key", json={"provider": "gemini", "api_key": "sk-secret-9876"}).json() == {
        "ok": True, "saved_key": True, "key_hint": "••••9876",
    }
    assert oct((tmp_path / "home" / "keys.json").stat().st_mode & 0o777) == "0o600"

    status = client.get("/api/ai/status?provider=gemini")
    assert status.json()["saved_key"] is True
    assert "sk-secret" not in status.text

    client.request("DELETE", "/api/settings/key?provider=gemini")
    assert client.get("/api/ai/status?provider=gemini").json()["saved_key"] is False


def test_robot_footage_keeps_description_unprefixed(monkeypatch, tmp_path: Path) -> None:
    """Only manual work gets the prefix — robot footage is the default case."""
    from video_reviewer import gemini_review

    manifest = _manifest_with_frame(tmp_path)
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.setattr(
        "video_reviewer.ai_review.providers.gemini.GeminiProvider._generate",
        lambda self, key, model, prompt, frames: {
            "description": "Sanding Metal Panel",
            "client_or_location": "GMR HQ",
            "is_manual": False,
            "confidence": 0.92,
            "rationale": "Robot sanding a panel.",
            "flags": [],
        },
    )

    result = gemini_review.review_rows(manifest, [0], api_key="test-key")[0]
    assert result.proposed_name == "2024-07_Sanding Metal Panel_GMR HQ_001.mov"


def test_naming_guide_reaches_the_provider_prompt(tmp_path: Path, monkeypatch) -> None:
    """The operator's guide is what steers naming, so it must be in the prompt."""
    from video_reviewer import naming_guide
    from video_reviewer.ai_review.models import ReviewPolicy, ReviewRequest
    from video_reviewer.ai_review.providers.common import prompt_for

    guide = tmp_path / "naming_guide.md"
    guide.write_text("# Naming guide\nAlways call the client Acme {not a format field}.\n", encoding="utf-8")
    monkeypatch.setattr(naming_guide, "GUIDE_PATH", guide)

    prompt = prompt_for(ReviewRequest(
        source_name="clip.mov", year_month="2024-07", capture_time="",
        source_hints={}, frames=[], policy=ReviewPolicy(),
    ))

    # Braces in operator-written markdown must survive rather than blow up .format().
    assert "Always call the client Acme {not a format field}." in prompt


def _chat_manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "manifest.csv"
    frame = tmp_path / "frame_00.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0jpegbytes")
    write_manifest_csv(manifest, [ManifestRow(
        source_path=str(tmp_path / "clip.mov"),
        sample_frames=str(frame),
        year_month="2024-07",
        description="Sanding Metal Panel",
        client_or_location="GMR HQ",
        sequence="001",
    )])
    return manifest


def test_chat_proposes_fields_without_touching_the_manifest(tmp_path: Path, monkeypatch) -> None:
    """The assistant proposes; only the operator's Approve writes the manifest."""
    from video_reviewer.ai_review.chat import chat_about_row

    manifest = _chat_manifest(tmp_path)
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.setattr(
        "video_reviewer.ai_review.providers.gemini.GeminiProvider._generate",
        lambda self, key, model, prompt, frames: {
            "message": "That is a fire truck panel, not a metal panel.",
            "set_fields": {"description": "Sanding Fire Truck Panel"},
            "remember": None,
        },
    )

    reply = chat_about_row(manifest, 0, [{"role": "user", "content": "what is this?"}], api_key="k")

    assert reply.set_fields == {"description": "Sanding Fire Truck Panel"}
    assert reply.remembered == ""
    # Unchanged on disk: the proposal only reaches the form.
    assert read_manifest_csv(manifest)[0].description == "Sanding Metal Panel"


def test_chat_remember_appends_one_rule_to_the_naming_guide(tmp_path: Path, monkeypatch) -> None:
    from video_reviewer import naming_guide
    from video_reviewer.ai_review.chat import chat_about_row

    guide = tmp_path / "naming_guide.md"
    guide.write_text("# Naming guide\n\nBase rules.\n", encoding="utf-8")
    monkeypatch.setattr(naming_guide, "GUIDE_PATH", guide)
    monkeypatch.setattr("video_reviewer.ai_review.chat.GUIDE_PATH", guide)

    manifest = _chat_manifest(tmp_path)
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.setattr(
        "video_reviewer.ai_review.providers.gemini.GeminiProvider._generate",
        lambda self, key, model, prompt, frames: {
            "message": "Noted.",
            "set_fields": None,
            "remember": "The client Ursa LBrothers is spelled with a capital B.",
        },
    )

    reply = chat_about_row(manifest, 0, [{"role": "user", "content": "it's LBrothers"}], api_key="k")

    assert reply.remembered.startswith("The client Ursa LBrothers")
    text = guide.read_text(encoding="utf-8")
    assert "## Operator notes" in text
    assert "- The client Ursa LBrothers is spelled with a capital B" in text

    # Saying the same thing twice must not stack duplicate lines.
    chat_about_row(manifest, 0, [{"role": "user", "content": "again"}], api_key="k")
    assert guide.read_text(encoding="utf-8").count("capital B") == 1


def test_chat_sees_the_clip_and_the_guide(tmp_path: Path, monkeypatch) -> None:
    """Whatever the operator writes in the guide must reach the conversation."""
    from video_reviewer import naming_guide
    from video_reviewer.ai_review.chat import chat_about_row

    guide = tmp_path / "naming_guide.md"
    guide.write_text("# Naming guide\n\nPrefer the term Buff and Polish.\n", encoding="utf-8")
    monkeypatch.setattr(naming_guide, "GUIDE_PATH", guide)

    captured = {}
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.setattr(
        "video_reviewer.ai_review.providers.gemini.GeminiProvider._generate",
        lambda self, key, model, prompt, frames: captured.update(prompt=prompt, frames=len(frames))
        or {"message": "ok", "set_fields": None, "remember": None},
    )

    chat_about_row(_chat_manifest(tmp_path), 0, [{"role": "user", "content": "which term?"}], api_key="k")

    assert "Prefer the term Buff and Polish." in captured["prompt"]
    assert "Sanding Metal Panel" in captured["prompt"]      # the clip's current fields
    assert "Operator: which term?" in captured["prompt"]     # the conversation
    assert captured["frames"] == 1


def test_chat_can_propose_an_explicit_all_clips_client_change(tmp_path: Path, monkeypatch) -> None:
    from video_reviewer.ai_review.chat import chat_about_row

    manifest = _chat_manifest(tmp_path)
    rows = read_manifest_csv(manifest)
    rows.append(ManifestRow(
        source_path=str(tmp_path / "clip-two.mov"), year_month="2024-07",
        description="Cleaning Metal Panel", client_or_location="Old Client", sequence="001",
    ))
    write_manifest_csv(manifest, rows)
    captured = {}
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)
    monkeypatch.setattr(
        "video_reviewer.ai_review.providers.gemini.GeminiProvider._generate",
        lambda self, key, model, prompt, frames: captured.update(prompt=prompt) or {
            "message": "I will propose that update for the whole batch.",
            "set_fields": None,
            "set_batch_fields": {"client_or_location": "Acme Robotics"},
            "remember": None,
        },
    )

    reply = chat_about_row(
        manifest, 0, [{"role": "user", "content": "Change the customer name in all videos to Acme Robotics."}], api_key="k",
    )

    assert reply.set_batch_fields == {"client_or_location": "Acme Robotics"}
    assert "clip-two.mov" in captured["prompt"]
    assert "Change the customer name in all videos" in captured["prompt"]
    # As with a per-clip proposal, browser review/approval still owns persistence.
    assert read_manifest_csv(manifest)[1].client_or_location == "Old Client"


def test_chat_rejects_an_empty_question(tmp_path: Path) -> None:
    from video_reviewer.ai_review import AiReviewError
    from video_reviewer.ai_review.chat import chat_about_row

    try:
        chat_about_row(_chat_manifest(tmp_path), 0, [{"role": "user", "content": "   "}], api_key="k")
    except AiReviewError as exc:
        assert "Ask a question" in str(exc)
    else:
        raise AssertionError("Expected an empty question to be rejected")


def _chat_spy(monkeypatch, payload):
    """Stub the SDK and capture what each call was actually given."""
    calls = []
    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._sdk_installed", lambda self: True)

    def generate(self, key, model, prompt, frames):
        calls.append({"prompt": prompt, "frames": len(frames)})
        return payload(len(calls))

    monkeypatch.setattr("video_reviewer.ai_review.providers.gemini.GeminiProvider._generate", generate)
    return calls


def test_chat_sends_frames_once_and_reuses_its_notes(tmp_path: Path, monkeypatch) -> None:
    """Images cross the wire on the first turn only; later turns carry the notes."""
    from video_reviewer.ai_review.chat import chat_about_row

    manifest = _chat_manifest(tmp_path)
    calls = _chat_spy(monkeypatch, lambda n: {
        "message": f"answer {n}",
        "set_fields": None,
        "remember": None,
        "frame_notes": "A robot sanding a metal enclosure." if n == 1 else None,
    })

    first = chat_about_row(manifest, 0, [{"role": "user", "content": "what is this?"}], api_key="k")
    assert calls[0]["frames"] > 0
    assert first.frame_notes == "A robot sanding a metal enclosure."

    second = chat_about_row(
        manifest, 0,
        [{"role": "user", "content": "what is this?"}, {"role": "assistant", "content": "answer 1"},
         {"role": "user", "content": "shorter please"}],
        api_key="k", frame_notes=first.frame_notes,
    )
    assert calls[1]["frames"] == 0, "the second turn must not re-send the images"
    assert "A robot sanding a metal enclosure." in calls[1]["prompt"]
    # The notes carry forward even when the model does not restate them.
    assert second.frame_notes == first.frame_notes


def test_chat_asks_for_frames_back_when_its_notes_fall_short(tmp_path: Path, monkeypatch) -> None:
    from video_reviewer.ai_review.chat import chat_about_row

    manifest = _chat_manifest(tmp_path)
    _chat_spy(monkeypatch, lambda n: {
        "message": "I cannot tell from my notes.",
        "set_fields": None, "remember": None, "need_frames": True,
    })

    # With notes in hand the model may ask to look again...
    assert chat_about_row(manifest, 0, [{"role": "user", "content": "what colour?"}],
                          api_key="k", frame_notes="Some notes.").need_frames is True
    # ...but on a turn that already carried the frames the flag is meaningless.
    assert chat_about_row(manifest, 0, [{"role": "user", "content": "what colour?"}],
                          api_key="k").need_frames is False
