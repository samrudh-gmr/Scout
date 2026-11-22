from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from video_reviewer.ai_review.models import AiReviewError, ErrorCategory, ProviderConfig
from video_reviewer.ai_review.providers.gemini import GeminiProvider
from video_reviewer.manifest import ManifestRow, write_manifest_csv


class FakeApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("video_reviewer.ai_review.providers.common.time.sleep", lambda _: None)


def _recording_client(monkeypatch, *, error: Exception | None = None):
    """Replace genai.Client; return the list of configs passed to generate_content."""
    sent: list = []
    constructed: list[int] = []

    class FakeClient:
        def __init__(self, api_key: str, **kwargs) -> None:
            constructed.append(1)
            self.models = SimpleNamespace(generate_content=self._generate_content)

        def _generate_content(self, *, model, contents, config):
            sent.append(config)
            if error is not None:
                raise error
            return SimpleNamespace(text="{}")

    monkeypatch.setattr("google.genai.Client", FakeClient)
    return sent, constructed


def test_unset_thinking_level_sends_no_thinking_config(monkeypatch) -> None:
    sent, _ = _recording_client(monkeypatch)
    GeminiProvider().generate_json("p", [], ProviderConfig(api_key="k"))
    assert len(sent) == 1
    assert sent[0].thinking_config is None


def test_chosen_thinking_level_reaches_the_request_config(monkeypatch) -> None:
    from google.genai import types

    sent, _ = _recording_client(monkeypatch)
    GeminiProvider().generate_json("p", [], ProviderConfig(api_key="k", thinking_level="low"))
    assert sent[0].thinking_config is not None
    assert sent[0].thinking_config.thinking_level == types.ThinkingLevel.LOW


def test_thinking_levels_come_from_the_installed_sdk() -> None:
    from google.genai import types

    expected = [level.value.lower() for level in types.ThinkingLevel if level.value != "THINKING_LEVEL_UNSPECIFIED"]
    assert GeminiProvider().thinking_levels() == expected
    assert "low" in expected


def test_unknown_thinking_level_is_rejected_before_any_call(monkeypatch) -> None:
    sent, constructed = _recording_client(monkeypatch)
    with pytest.raises(AiReviewError) as caught:
        GeminiProvider().generate_json("p", [], ProviderConfig(api_key="k", thinking_level="extreme"))
    assert caught.value.category == ErrorCategory.VALIDATION
    message = str(caught.value)
    assert "extreme" in message and "low" in message
    assert constructed == [] and sent == []


def test_rejected_thinking_level_is_an_invalid_request_and_not_retried(monkeypatch) -> None:
    sent, _ = _recording_client(
        monkeypatch, error=FakeApiError(400, "INVALID_ARGUMENT. Thinking level is not supported for this model."),
    )
    with pytest.raises(AiReviewError) as caught:
        GeminiProvider().generate_json(
            "p", [], ProviderConfig(model="gemini-3.5-flash-lite", api_key="k", thinking_level="minimal"), max_retries=2,
        )
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert len(sent) == 1
    assert "minimal" in str(caught.value)


def test_cli_thinking_option_parses_and_reaches_the_review(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer import cli

    args = cli.build_parser().parse_args(["ai-review", "--manifest", "m.csv", "--thinking", "high"])
    assert args.thinking == "high"
    assert cli.build_parser().parse_args(["ai-review", "--manifest", "m.csv"]).thinking == ""
    assert cli.build_parser().parse_args(["gemini-review", "--manifest", "m.csv", "--thinking", "low"]).thinking == "low"

    seen: dict = {}

    def fake_review(manifest_path, indices, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr("video_reviewer.ai_review.review_rows_with_ai", fake_review)
    manifest = tmp_path / "m.csv"
    write_manifest_csv(manifest, [ManifestRow(source_path=str(tmp_path / "a.mov"))])
    args = cli.build_parser().parse_args(["ai-review", "--manifest", str(manifest), "--indices", "0", "--thinking", "high"])
    args.func(args)
    assert seen["thinking_level"] == "high"


def test_review_passes_thinking_level_to_the_provider(monkeypatch, tmp_path: Path) -> None:
    from video_reviewer.ai_review import service

    seen: list[ProviderConfig] = []
    monkeypatch.setattr(service, "_review_one_row", lambda manifest, index, provider, config, policy, *rest: seen.append(config))
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    service.review_rows_with_ai(tmp_path / "m.csv", [0], provider_id="gemini", api_key="k", thinking_level="medium")
    assert seen[0].thinking_level == "medium"


def _client(monkeypatch, tmp_path: Path):
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [ManifestRow(source_path=str(tmp_path / "a.mov"))])
    monkeypatch.setattr("video_reviewer.gui.load_api_key", lambda provider: "k")
    monkeypatch.setattr("video_reviewer.gui.api_key_hint", lambda provider: "••••")
    return TestClient(create_app(manifest))


def test_status_offers_thinking_levels_only_for_gemini(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(GeminiProvider, "list_models", lambda self, api_key=None: ["gemini-4-hydrogen"])
    client = _client(monkeypatch, tmp_path)
    gemini = client.get("/api/ai/status?provider=gemini").json()
    assert gemini["thinking_levels"] == GeminiProvider().thinking_levels()
    other = client.get("/api/ai/status?provider=anthropic").json()
    assert other["thinking_levels"] == []


def test_review_route_forwards_thinking_level(monkeypatch, tmp_path: Path) -> None:
    seen: dict = {}

    def fake_review(manifest_path, indices, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr("video_reviewer.ai_review.review_rows_with_ai", fake_review)
    client = _client(monkeypatch, tmp_path)
    client.post("/api/ai/review", json={"indices": [0], "provider": "gemini", "thinking_level": "low"})
    assert seen["thinking_level"] == "low"
    client.post("/api/ai/review", json={"indices": [0], "provider": "gemini"})
    assert seen["thinking_level"] is None
