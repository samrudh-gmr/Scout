from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from video_reviewer import app_log
from video_reviewer.ai_review.models import AiReviewError, ProviderConfig
from video_reviewer.ai_review.providers.gemini import GeminiProvider
from video_reviewer.manifest import REVIEW_PENDING, ManifestRow, write_manifest_csv

SECRET = "AIzaSyTestKeyTestKeyTestKey1234567890"
PAYLOAD = {
    "scene": "robot_process", "part": "Aluminum Wheel", "industry": "Specialty Vehicle", "description": "Sanding",
    "client_or_location": "GMR HQ", "confidence": 0.95, "rationale": "robot sands a wheel", "flags": [],
}


class FakeApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code


@pytest.fixture(autouse=True)
def _clean_handlers(monkeypatch):
    monkeypatch.setattr("video_reviewer.ai_review.providers.common.time.sleep", lambda _: None)
    yield
    logger = logging.getLogger(app_log.LOGGER_NAME)
    for handler in [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]:
        logger.removeHandler(handler)
        handler.close()


def _manifest(tmp_path: Path) -> Path:
    frame = tmp_path / "clip_frame_00.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    source = tmp_path / "clip.mov"
    source.write_bytes(b"0")
    manifest = tmp_path / "batch.csv"
    write_manifest_csv(manifest, [ManifestRow(
        source_path=str(source.resolve()), sample_frames=str(frame.resolve()), year_month="2024-07",
        sequence="001", review_status=REVIEW_PENDING, source_hints=json.dumps({}),
    )])
    return manifest


def test_log_file_is_set_up_once_and_is_size_capped(tmp_path) -> None:
    first = app_log.setup_logging(tmp_path / "logs")
    second = app_log.setup_logging(tmp_path / "logs")
    assert first == second == tmp_path / "logs" / "scout.log"
    handlers = [h for h in logging.getLogger(app_log.LOGGER_NAME).handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1
    assert handlers[0].maxBytes > 0 and handlers[0].backupCount > 0
    logging.getLogger(app_log.LOGGER_NAME).info("hello from the test")
    handlers[0].flush()
    assert "hello from the test" in first.read_text(encoding="utf-8")


def test_a_log_folder_that_cannot_be_made_does_not_stop_the_app(tmp_path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert app_log.setup_logging(blocker / "logs") is None


def test_key_like_text_never_reaches_the_log_file(tmp_path) -> None:
    path = app_log.setup_logging(tmp_path / "logs")
    logger = logging.getLogger(app_log.LOGGER_NAME)
    logger.warning("provider said: bad key %s and Bearer abcdefghijklmnopqrstuvwxyz012345", SECRET)
    for handler in logger.handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text and "abcdefghijklmnopqrstuvwxyz012345" not in text
    assert "[redacted]" in text


def test_each_failed_attempt_logs_the_status_and_the_provider_text_without_the_key(monkeypatch, caplog) -> None:
    """Seen live: 'rate limit, try fewer videos' hid a 402 and, later, a 429 usage limit."""
    def _generate(self, key, model, prompt, frames):
        raise FakeApiError(429, f"The usage limit has been reached (key {key})")

    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _generate)
    with caplog.at_level(logging.INFO, logger=app_log.LOGGER_NAME):
        with pytest.raises(AiReviewError):
            GeminiProvider().generate_json("p", [], ProviderConfig(model="gemini-test", api_key=SECRET), max_retries=1)
    failed = [r.getMessage() for r in caplog.records if "model_call failed" in r.getMessage()]
    assert len(failed) == 2
    assert "attempt=1/2" in failed[0] and "attempt=2/2" in failed[1]
    assert all("http_status=429" in line and "category=rate_limit" in line for line in failed)
    assert all("The usage limit has been reached" in line and "model=gemini-test" in line for line in failed)
    assert "will_retry=True" in failed[0] and "will_retry=False" in failed[1]
    assert SECRET not in "\n".join(failed)


def test_a_successful_call_logs_model_seconds_and_tokens(monkeypatch, caplog) -> None:
    def _generate(self, key, model, prompt, frames):
        return {**PAYLOAD, "_raw_text": "{}", "_usage": {"prompt_tokens": 1200, "thoughts_tokens": 300, "output_tokens": 80}}

    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _generate)
    with caplog.at_level(logging.INFO, logger=app_log.LOGGER_NAME):
        GeminiProvider().generate_json("the prompt text", [], ProviderConfig(model="gemini-test", api_key=SECRET))
    lines = [r.getMessage() for r in caplog.records if "model_call ok" in r.getMessage()]
    assert len(lines) == 1
    assert "provider=gemini" in lines[0] and "model=gemini-test" in lines[0] and "seconds=" in lines[0]
    assert "prompt_tokens=1200" in lines[0] and "thoughts_tokens=300" in lines[0] and "output_tokens=80" in lines[0]
    assert "the prompt text" not in lines[0]


def test_each_reviewed_clip_logs_one_summary_line(tmp_path, monkeypatch, caplog) -> None:
    def _generate(self, key, model, prompt, frames):
        return {**PAYLOAD, "_raw_text": json.dumps(PAYLOAD), "_usage": None}

    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _generate)
    from video_reviewer.ai_review import review_rows_with_ai

    with caplog.at_level(logging.INFO, logger=app_log.LOGGER_NAME):
        review_rows_with_ai(_manifest(tmp_path), [0], provider_id="gemini", model="gemini-test", api_key=SECRET)
    lines = [r.getMessage() for r in caplog.records]
    clips = [line for line in lines if line.startswith("review_clip ")]
    assert len(clips) == 1
    assert "clip=clip.mov" in clips[0] and "status=approved" in clips[0] and "seconds=" in clips[0]
    assert any(line.startswith("review_batch start") and "clips=1" in line and "model=gemini-test" in line for line in lines)
    assert any(line.startswith("review_batch done") and "approved=1" in line for line in lines)


def test_the_command_line_turns_the_log_file_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_log, "default_log_dir", lambda: tmp_path / "logs")
    from video_reviewer import cli

    assert cli.main(["apply", "--manifest", str(tmp_path / "missing.csv"), "--dry-run"]) == 1
    assert (tmp_path / "logs" / "scout.log").exists()


def test_the_desktop_app_turns_the_log_file_on_and_logs_a_failed_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_log, "default_log_dir", lambda: tmp_path / "logs")
    from video_reviewer import desktop

    def _boom(*args, **kwargs):
        raise RuntimeError("window could not open")

    monkeypatch.setattr(desktop, "launch_desktop", _boom)
    assert desktop.main() == 1
    for handler in logging.getLogger(app_log.LOGGER_NAME).handlers:
        handler.flush()
    assert "window could not open" in (tmp_path / "logs" / "scout.log").read_text(encoding="utf-8")

