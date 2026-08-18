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
        lambda self, key, model, request: {
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
    assert result.proposed_name == "2024-07_Sanding Metal Panel_GMR HQ_001.mov"
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
        lambda self, key, model, request: {
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
        lambda self, key, model, request: {
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

    def boom(self, key, model, request):
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
