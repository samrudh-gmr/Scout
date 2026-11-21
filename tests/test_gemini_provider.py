from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from video_reviewer.ai_review.models import AiReviewError, ErrorCategory, ProviderConfig
from video_reviewer.ai_review.providers.gemini import GeminiProvider
from video_reviewer.manifest import write_manifest_csv


class FakeApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code


@pytest.fixture(autouse=True)
def _no_sleep_and_fresh_cache(monkeypatch):
    monkeypatch.setattr("video_reviewer.ai_review.providers.common.time.sleep", lambda _: None)
    GeminiProvider._model_cache.clear()


def _failing_generate(calls: list[int], exc: Exception):
    def _generate(self, key, model, prompt, frames):
        calls.append(1)
        raise exc

    return _generate


def test_gemini_defaults_to_hydrogen() -> None:
    provider = GeminiProvider()
    assert provider.default_model == "gemini-4-hydrogen"
    assert provider.cheap_model == "gemini-3.5-flash-lite"
    assert provider.accurate_model == "gemini-4-carbon"


def test_legacy_wrapper_uses_the_provider_default() -> None:
    from video_reviewer import gemini_review

    assert gemini_review.DEFAULT_MODEL == GeminiProvider.default_model


def test_unknown_model_is_reported_as_a_request_error_and_not_retried(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(
        GeminiProvider,
        "_generate",
        _failing_generate(calls, FakeApiError(404, "NOT_FOUND. models/gemini-9-nope is not found for generateContent")),
    )
    with pytest.raises(AiReviewError) as caught:
        GeminiProvider().generate_json("p", [], ProviderConfig(model="gemini-9-nope", api_key="k"), max_retries=2)
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert "gemini-9-nope" in str(caught.value)
    assert "temporarily unavailable" not in str(caught.value)
    assert len(calls) == 1


def test_bad_request_is_not_retried(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _failing_generate(calls, FakeApiError(400, "INVALID_ARGUMENT. bad config")))
    with pytest.raises(AiReviewError) as caught:
        GeminiProvider().generate_json("p", [], ProviderConfig(api_key="k"), max_retries=2)
    assert caught.value.category == ErrorCategory.INVALID_REQUEST
    assert len(calls) == 1


def test_invalid_key_400_is_still_an_invalid_key(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(
        GeminiProvider, "_generate", _failing_generate(calls, FakeApiError(400, "INVALID_ARGUMENT. API key not valid."))
    )
    with pytest.raises(AiReviewError) as caught:
        GeminiProvider().generate_json("p", [], ProviderConfig(api_key="k"), max_retries=2)
    assert caught.value.category == ErrorCategory.INVALID_KEY
    assert len(calls) == 1


def test_server_error_is_retried_then_reported_unavailable(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _failing_generate(calls, FakeApiError(503, "UNAVAILABLE. overloaded")))
    with pytest.raises(AiReviewError) as caught:
        GeminiProvider().generate_json("p", [], ProviderConfig(api_key="k"), max_retries=2)
    assert caught.value.category == ErrorCategory.PROVIDER_UNAVAILABLE
    assert len(calls) == 3


def test_the_word_generate_is_not_mistaken_for_a_rate_limit(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "_generate", _failing_generate(calls, RuntimeError("generate_content failed: socket closed")))
    with pytest.raises(AiReviewError) as caught:
        GeminiProvider().generate_json("p", [], ProviderConfig(api_key="k"), max_retries=0)
    assert caught.value.category == ErrorCategory.PROVIDER_UNAVAILABLE


def _fake_client(models, counter: list[int] | None = None):
    class FakeClient:
        def __init__(self, api_key: str, **kwargs) -> None:
            self.models = SimpleNamespace(list=self._list)

        def _list(self):
            if counter is not None:
                counter.append(1)
            if isinstance(models, Exception):
                raise models
            return models

    return FakeClient


def test_list_models_keeps_only_gemini_models_that_can_name_clips(monkeypatch) -> None:
    listed = [
        SimpleNamespace(name="models/gemini-4-neon", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/gemini-4-hydrogen", supported_actions=["generateContent", "countTokens"]),
        SimpleNamespace(name="models/gemini-3.8-flash-tts", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/gemini-3.1-flash-image", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/gemini-3.8-live", supported_actions=["bidiGenerateContent"]),
        SimpleNamespace(name="models/gemini-embedding-2", supported_actions=["embedContent"]),
        SimpleNamespace(name="models/genie-4", supported_actions=["generateContent"]),
    ]
    monkeypatch.setattr("google.genai.Client", _fake_client(listed))
    assert GeminiProvider().list_models("k") == ["gemini-4-hydrogen", "gemini-4-neon"]


def test_list_models_is_cached_per_key(monkeypatch) -> None:
    counter: list[int] = []
    listed = [SimpleNamespace(name="models/gemini-4-hydrogen", supported_actions=["generateContent"])]
    monkeypatch.setattr("google.genai.Client", _fake_client(listed, counter))
    provider = GeminiProvider()
    provider.list_models("k")
    provider.list_models("k")
    assert len(counter) == 1
    provider.list_models("other")
    assert len(counter) == 2


def test_list_models_without_a_key_makes_no_request(monkeypatch) -> None:
    counter: list[int] = []
    monkeypatch.setattr("google.genai.Client", _fake_client([], counter))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert GeminiProvider().list_models(None) == []
    assert counter == []


def test_status_does_not_call_the_network(monkeypatch) -> None:
    counter: list[int] = []
    monkeypatch.setattr("google.genai.Client", _fake_client([], counter))
    status = GeminiProvider().status(ProviderConfig(api_key="k"))
    assert status.available and status.has_key
    assert counter == []


def _status_client(monkeypatch, tmp_path: Path):
    from fastapi.testclient import TestClient
    from video_reviewer.gui import create_app

    manifest = tmp_path / "manifest.csv"
    write_manifest_csv(manifest, [])
    monkeypatch.setattr("video_reviewer.gui.load_api_key", lambda provider: "k")
    monkeypatch.setattr("video_reviewer.gui.api_key_hint", lambda provider: "••••")
    return TestClient(create_app(manifest))


def test_status_endpoint_offers_live_models_for_the_picker(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr(GeminiProvider, "list_models", lambda self, api_key=None: ["gemini-4-hydrogen", "gemini-4-neon"])
    body = _status_client(monkeypatch, tmp_path).get("/api/ai/status?provider=gemini").json()
    assert body["default_model"] == "gemini-4-hydrogen"
    assert body["models"] == ["gemini-4-hydrogen", "gemini-4-neon"]


def test_status_endpoint_falls_back_to_known_models_when_listing_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(GeminiProvider, "_sdk_installed", lambda self: True)
    monkeypatch.setattr("google.genai.Client", _fake_client(RuntimeError("offline")))
    body = _status_client(monkeypatch, tmp_path).get("/api/ai/status?provider=gemini").json()
    assert body["available"] is True
    assert body["models"] == ["gemini-4-hydrogen", "gemini-3.5-flash-lite", "gemini-4-carbon"]
