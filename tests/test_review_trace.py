from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from video_reviewer.ai_review.models import (
    AiReviewError,
    ErrorCategory,
    ProviderConfig,
    ReviewPolicy,
    ReviewRequest,
    ReviewResponse,
)
from video_reviewer.ai_review.providers.common import parse_response
from video_reviewer.ai_review.providers.gemini import GeminiProvider
from video_reviewer.manifest import REVIEW_PENDING, ManifestRow, read_manifest_csv, write_manifest_csv

SECRET = "sk-test-SECRET-do-not-log-1234567890"
CHOICES = ["RV Front Cap", "Truck Hood"]
PAYLOAD = {
    "scene": "robot_process", "part": "Aluminum Wheel", "industry": "Specialty Vehicle", "description": "Sanding",
    "client_or_location": "GMR HQ", "confidence": 0.95, "rationale": "robot sands a wheel", "flags": [],
}
USAGE = {"prompt_tokens": 1200, "thoughts_tokens": 300, "output_tokens": 80, "total_tokens": 1580}

FIELDS = {
    "trace_version", "timestamp", "source_name", "provider", "model", "prompt_sha256", "prompt", "frames",
    "raw_reply", "response", "context", "decision", "usage", "wall_seconds", "status", "proposed_name",
    "error_category", "error",
}


def _manifest(tmp_path: Path, names: tuple[str, ...] = ("clip.mov",), hints: dict | None = None) -> Path:
    rows = []
    for name in names:
        frame = tmp_path / f"{Path(name).stem}_frame_00.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xd9")
        source = tmp_path / name
        source.write_bytes(b"0")
        rows.append(ManifestRow(
            source_path=str(source.resolve()), sample_frames=str(frame.resolve()), year_month="2024-07",
            sequence="001", review_status=REVIEW_PENDING, source_hints=json.dumps(hints or {}),
        ))
    manifest = tmp_path / "batch.csv"
    write_manifest_csv(manifest, rows)
    return manifest


def _fake_gemini(monkeypatch, payload: dict | None = None, error: AiReviewError | None = None, seen: list | None = None):
    def _generate(self, key, model, prompt, frames):
        if seen is not None:
            seen.append(key)
        if error is not None:
            raise error
        data = dict(payload or PAYLOAD)
        data["_raw_text"] = json.dumps(payload or PAYLOAD)
        data["_usage"] = dict(USAGE)
        return data

    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _generate)


def _review(manifest: Path, indices=(0,), api_key: str = "k"):
    from video_reviewer.ai_review import review_rows_with_ai

    return review_rows_with_ai(manifest, list(indices), provider_id="gemini", model="gemini-test", api_key=api_key)


def _lines(manifest: Path) -> list[dict]:
    path = manifest.with_name(f"{manifest.stem}.trace.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_successful_review_writes_one_complete_line(monkeypatch, tmp_path: Path) -> None:
    import hashlib

    manifest = _manifest(tmp_path)
    _fake_gemini(monkeypatch)
    result = _review(manifest)[0]

    lines = _lines(manifest)
    assert len(lines) == 1
    line = lines[0]
    assert set(line) == FIELDS
    assert line["source_name"] == "clip.mov"
    assert line["provider"] == "gemini" and line["model"] == "gemini-test"
    assert line["timestamp"].endswith("+00:00")
    assert line["prompt"] and line["prompt_sha256"] == hashlib.sha256(line["prompt"].encode("utf-8")).hexdigest()
    assert line["frames"] == ["clip_frame_00.jpg"]
    assert json.loads(line["raw_reply"]) == PAYLOAD
    assert line["response"]["part"] == "Aluminum Wheel" and line["response"]["scene"] == "robot_process"
    assert line["decision"]["scene"] == "robot_process"
    assert line["decision"]["auto_approve"] is True
    assert line["decision"]["flags"] == []
    assert line["usage"] == USAGE
    assert isinstance(line["wall_seconds"], float) and line["wall_seconds"] >= 0
    assert line["status"] == result.status and line["proposed_name"] == result.proposed_name
    assert line["error_category"] == "" and line["error"] == ""


def test_failed_review_writes_a_line_with_the_error_category(monkeypatch, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    _fake_gemini(monkeypatch, error=AiReviewError("Gemini API rate limit or quota reached.", ErrorCategory.RATE_LIMIT))
    result = _review(manifest)[0]

    assert result.error_category == "rate_limit"
    [line] = _lines(manifest)
    assert line["error_category"] == "rate_limit"
    assert "rate limit" in line["error"]
    assert line["decision"] is None and line["response"] is None and line["usage"] is None
    assert line["frames"] == ["clip_frame_00.jpg"] and line["prompt_sha256"]


def test_two_reviews_append_two_lines(monkeypatch, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, names=("a.mov", "b.mov"))
    _fake_gemini(monkeypatch)
    _review(manifest, indices=(0,))
    _review(manifest, indices=(1,))

    assert [line["source_name"] for line in _lines(manifest)] == ["a.mov", "b.mov"]


def test_replaying_decide_from_a_trace_line_gives_the_recorded_decision(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer.ai_review.trace import replay_decision

    # Part choices and a batch client override both feed decide; replay must use them.
    manifest = _manifest(tmp_path, hints={"part_choices": CHOICES, "batch_client_override": "Life Line"})
    _fake_gemini(monkeypatch, payload={**PAYLOAD, "part": "truck hood", "flags": ["blurry_frames"]})
    _review(manifest)

    [line] = _lines(manifest)
    assert line["decision"]["part"] == "Truck Hood"
    assert line["decision"]["client_or_location"] == "Life Line"
    assert line["context"]["part_choices"] == CHOICES
    replayed = replay_decision(line)
    assert replayed == replay_decision(json.dumps(line))
    from dataclasses import asdict

    assert asdict(replayed) == line["decision"]


def test_unwritable_trace_path_leaves_the_review_result_unchanged(monkeypatch, tmp_path: Path) -> None:
    traced_dir = tmp_path / "traced"
    blocked_dir = tmp_path / "blocked"
    traced_dir.mkdir()
    blocked_dir.mkdir()
    _fake_gemini(monkeypatch)

    expected = _review(_manifest(traced_dir))[0]
    blocked = _manifest(blocked_dir)
    # A directory where the trace file should go makes every open() fail.
    blocked.with_name(f"{blocked.stem}.trace.jsonl").mkdir()
    result = _review(blocked)[0]

    assert result == expected
    assert read_manifest_csv(blocked)[0].proposed_name == expected.proposed_name


def test_trace_serialization_failure_is_swallowed(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer.ai_review import trace

    manifest = _manifest(tmp_path)
    _fake_gemini(monkeypatch)

    real_build = trace.build_trace_line
    monkeypatch.setattr(trace, "build_trace_line", lambda **fields: {**real_build(**fields), "bad": object()})
    result = _review(manifest)[0]
    assert result.ok is True
    assert not manifest.with_name(f"{manifest.stem}.trace.jsonl").exists()


def test_trace_line_never_contains_the_api_key(monkeypatch, tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    seen: list[str] = []
    _fake_gemini(monkeypatch, seen=seen)
    _review(manifest, api_key=SECRET)

    assert seen == [SECRET]  # the key really reached the provider
    raw = manifest.with_name(f"{manifest.stem}.trace.jsonl").read_text(encoding="utf-8")
    assert SECRET not in raw
    assert "SECRET" not in raw


# ── provider usage ───────────────────────────────────────────────────────────

REPLY = {"description": "Sanding", "client_or_location": "GMR HQ", "confidence": 0.9}


def _fake_client(metadata):
    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.models = SimpleNamespace(generate_content=self._generate)

        def _generate(self, **kwargs):
            return SimpleNamespace(text=json.dumps(REPLY), usage_metadata=metadata)

    return FakeClient


def _classify(monkeypatch, metadata) -> ReviewResponse:
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr("google.genai.Client", _fake_client(metadata))
    request = ReviewRequest(source_name="a.mov", year_month="2024-07", capture_time="", source_hints={}, frames=[],
                            policy=ReviewPolicy(max_retries=0))
    return GeminiProvider().classify(request, ProviderConfig(api_key="k"))


def test_gemini_reports_prompt_thoughts_output_and_total_tokens(monkeypatch) -> None:
    metadata = SimpleNamespace(prompt_token_count=1200, thoughts_token_count=300, candidates_token_count=80,
                               total_token_count=1580)
    response = _classify(monkeypatch, metadata)
    assert response.usage == {"prompt_tokens": 1200, "thoughts_tokens": 300, "output_tokens": 80, "total_tokens": 1580}
    assert json.loads(response.raw_text) == REPLY


def test_gemini_usage_counts_are_null_when_absent(monkeypatch) -> None:
    response = _classify(monkeypatch, SimpleNamespace(prompt_token_count=10, thoughts_token_count=None))
    assert response.usage == {"prompt_tokens": 10, "thoughts_tokens": None, "output_tokens": None, "total_tokens": None}
    assert _classify(monkeypatch, None).usage is None


def test_responses_without_usage_keep_working() -> None:
    assert parse_response(dict(REPLY)).usage is None
    assert ReviewResponse(description="d", client_or_location="c", is_manual=False, confidence=0.5).usage is None
