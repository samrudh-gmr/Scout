from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from video_reviewer.ai_review.models import AiReviewError, ErrorCategory
from video_reviewer.ai_review.prompt import PROMPT_TEMPLATE  # noqa: F401 - kept for older imports
from video_reviewer.ai_review.providers.common import VisionProviderBase

_MODEL_CACHE_SECONDS = 600
_MODEL_LIST_TIMEOUT_MS = 8000
# Gemini models that answer generateContent but cannot name a clip from frames.
_NON_VISION_MARKERS = ("tts", "image", "live", "embedding", "transcribe", "translate", "omni", "audio", "banana")


class GeminiProvider(VisionProviderBase):
    provider_id = "gemini"
    display_name = "Gemini API"
    default_model = "gemini-4-hydrogen"
    cheap_model = "gemini-3.5-flash-lite"
    accurate_model = "gemini-4-carbon"
    env_key_names = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    missing_sdk_message = "google-genai is not installed. Run `uv sync` or `pip install google-genai`."
    key_message = "Gemini SDK ready. Paste an API key or set GEMINI_API_KEY. Create one at https://aistudio.google.com/apikey"
    ready_message = "Gemini API ready."

    # key fingerprint -> (fetched at, model ids). Never holds the key itself.
    _model_cache: dict[str, tuple[float, list[str]]] = {}

    def _sdk_installed(self) -> bool:
        try:
            import google.genai  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self, api_key: str | None = None) -> list[str]:
        """Vision-capable Gemini models this key can use, or [] if they cannot be listed."""
        key = self.resolve_api_key(api_key)
        if not key or not self._sdk_installed():
            return []
        fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
        cached = self._model_cache.get(fingerprint)
        if cached and time.monotonic() - cached[0] < _MODEL_CACHE_SECONDS:
            return list(cached[1])
        try:
            from google import genai

            client = genai.Client(api_key=key, http_options={"timeout": _MODEL_LIST_TIMEOUT_MS})
            listed = list(client.models.list())
        except Exception:  # noqa: BLE001 - the picker falls back to the known models
            return []
        names: set[str] = set()
        for model in listed:
            name = str(getattr(model, "name", "") or "").removeprefix("models/")
            actions = getattr(model, "supported_actions", None) or []
            if not name.startswith("gemini-") or "generateContent" not in actions:
                continue
            if any(marker in name for marker in _NON_VISION_MARKERS):
                continue
            names.add(name)
        models = sorted(names)
        if models:
            self._model_cache[fingerprint] = (time.monotonic(), models)
        return models

    def _generate(self, api_key: str, model: str, prompt: str, frames) -> dict[str, Any]:
        from google import genai
        from google.genai import types

        parts: list[Any] = [types.Part.from_text(text=prompt)]
        for frame in frames:
            parts.append(types.Part.from_bytes(data=frame.data, mime_type=frame.mime_type))

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=parts,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.15),
        )
        text = (response.text or "").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AiReviewError(f"Model did not return valid JSON: {text[:160]}", ErrorCategory.MALFORMED_RESPONSE) from exc
        data["_raw_text"] = text
        data["_usage"] = _usage(getattr(response, "usage_metadata", None))
        return data


def _usage(metadata: Any) -> dict[str, int | None] | None:
    """Token counts from Gemini usage metadata; a count it omits is None."""
    if metadata is None:
        return None

    def count(name: str) -> int | None:
        value = getattr(metadata, name, None)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return {
        "prompt_tokens": count("prompt_token_count"),
        "thoughts_tokens": count("thoughts_token_count"),
        "output_tokens": count("candidates_token_count"),
        "total_tokens": count("total_token_count"),
    }
