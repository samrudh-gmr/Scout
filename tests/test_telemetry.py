from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from video_reviewer import telemetry
from video_reviewer.ai_review.models import AiReviewError, ProviderConfig
from video_reviewer.ai_review.providers.gemini import GeminiProvider
from video_reviewer.manifest import REVIEW_PENDING, ManifestRow, write_manifest_csv

SECRET = "AIzaSyTestKeyTestKeyTestKey1234567890"
PAYLOAD = {
    "scene": "robot_process", "part": "Aluminum Wheel", "industry": "Specialty Vehicle", "description": "Sanding",
    "client_or_location": "GMR HQ", "confidence": 0.95, "rationale": "robot sands a wheel", "flags": [],
}
USAGE = {"prompt_tokens": 1200, "thoughts_tokens": 300, "output_tokens": 80, "total_tokens": 1580}


class FakeApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code


@pytest.fixture
def spans(monkeypatch):
    monkeypatch.setattr("video_reviewer.ai_review.providers.common.time.sleep", lambda _: None)
    exporter = InMemorySpanExporter()
    assert telemetry.setup_telemetry(processor=SimpleSpanProcessor(exporter)) is True
    yield exporter
    telemetry.shutdown_telemetry()


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


def _ok(self, key, model, prompt, frames):
    return {**PAYLOAD, "_raw_text": json.dumps(PAYLOAD), "_usage": dict(USAGE)}


def test_telemetry_is_off_unless_an_endpoint_is_given(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setattr(telemetry, "read_config", lambda: {})
    assert telemetry.setup_telemetry() is False
    assert telemetry.enabled() is False
    with telemetry.span("anything", a=1) as current:
        current.set(b=2)  # a no-op, and never an error


def test_a_review_makes_batch_clip_and_model_call_spans_in_one_trace(tmp_path, monkeypatch, spans) -> None:
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _ok)
    from video_reviewer.ai_review import review_rows_with_ai

    review_rows_with_ai(_manifest(tmp_path), [0], provider_id="gemini", model="gemini-test", api_key=SECRET)
    done = {span.name: span for span in spans.get_finished_spans()}
    assert set(done) == {"scout.review_batch", "scout.review_clip", "chat gemini-test"}
    batch, clip, call = done["scout.review_batch"], done["scout.review_clip"], done["chat gemini-test"]
    assert clip.parent.span_id == batch.context.span_id
    assert call.parent.span_id == clip.context.span_id
    assert batch.attributes["scout.clips"] == 1
    assert clip.attributes["scout.clip"] == "clip.mov"
    assert clip.attributes["scout.status"] == "approved"
    assert call.attributes["gen_ai.operation.name"] == "chat"
    assert call.attributes["gen_ai.provider.name"] == "gemini"
    assert call.attributes["gen_ai.request.model"] == "gemini-test"
    assert call.attributes["gen_ai.usage.input_tokens"] == 1200
    assert call.attributes["gen_ai.usage.output_tokens"] == 80
    assert call.attributes["gen_ai.usage.reasoning.output_tokens"] == 300
    assert call.attributes["scout.frames"] == 1


def test_a_failed_call_is_one_error_span_per_attempt_with_the_status(monkeypatch, spans) -> None:
    def _generate(self, key, model, prompt, frames):
        raise FakeApiError(429, f"The usage limit has been reached (key {key})")

    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _generate)
    with pytest.raises(AiReviewError):
        GeminiProvider().generate_json("p", [], ProviderConfig(model="gemini-test", api_key=SECRET), max_retries=1)
    calls = [span for span in spans.get_finished_spans() if span.name == "chat gemini-test"]
    assert len(calls) == 2
    for number, call in enumerate(calls, start=1):
        assert call.status.status_code.name == "ERROR"
        assert call.attributes["error.type"] == "rate_limit"
        assert call.attributes["http.response.status_code"] == 429
        assert call.attributes["scout.attempt"] == number


def test_spans_never_carry_the_prompt_the_key_or_image_bytes(tmp_path, monkeypatch, spans) -> None:
    def _generate(self, key, model, prompt, frames):
        raise FakeApiError(400, f"bad request, key was {key}")

    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _generate)
    from video_reviewer.ai_review import review_rows_with_ai

    review_rows_with_ai(_manifest(tmp_path), [0], provider_id="gemini", model="gemini-test", api_key=SECRET)
    everything = json.dumps([
        [span.name, dict(span.attributes), span.status.description, [dict(event.attributes) for event in span.events]]
        for span in spans.get_finished_spans()
    ], default=str)
    assert SECRET not in everything
    assert "Review the attached frames" not in everything and "robot_process" not in everything
    assert "gen_ai.input.messages" not in everything and "gen_ai.prompt" not in everything


def test_a_broken_exporter_never_changes_a_review(tmp_path, monkeypatch) -> None:
    class Broken:
        def on_start(self, span, parent_context=None): raise RuntimeError("exporter down")
        def on_end(self, span): raise RuntimeError("exporter down")
        def _on_ending(self, span): raise RuntimeError("exporter down")
        def shutdown(self): pass
        def force_flush(self, timeout_millis=30000): return True

    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _ok)
    telemetry.setup_telemetry(processor=Broken())
    try:
        from video_reviewer.ai_review import review_rows_with_ai

        results = review_rows_with_ai(_manifest(tmp_path), [0], provider_id="gemini", model="gemini-test", api_key=SECRET)
    finally:
        telemetry.shutdown_telemetry()
    assert [r.status for r in results] == ["approved"]


def test_only_a_local_endpoint_is_accepted_unless_remote_is_allowed(monkeypatch) -> None:
    """Spans carry clip file names, so they stay on this machine by default."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("SCOUT_OTEL_ALLOW_REMOTE", raising=False)
    monkeypatch.setattr(telemetry, "read_config", lambda: {})
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.example.com:4318")
    assert telemetry.setup_telemetry() is False
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    try:
        assert telemetry.setup_telemetry() is True
    finally:
        telemetry.shutdown_telemetry()


def test_a_command_sends_its_buffered_spans_before_it_exits(tmp_path, monkeypatch) -> None:
    from video_reviewer import app_log, cli

    stopped: list[int] = []
    monkeypatch.setattr(app_log, "default_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(telemetry, "setup_telemetry", lambda processor=None: True)
    monkeypatch.setattr(telemetry, "shutdown_telemetry", lambda: stopped.append(1))
    assert cli.main(["apply", "--manifest", str(tmp_path / "missing.csv"), "--dry-run"]) == 1
    assert stopped == [1]
