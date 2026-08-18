from __future__ import annotations

import json
import os
import time
from typing import Any

from video_reviewer.ai_review.models import (
    AiReviewError,
    ErrorCategory,
    ProviderConfig,
    ProviderStatus,
    ReviewRequest,
    ReviewResponse,
)

PROMPT_TEMPLATE = """You are classifying a single manufacturing/industrial video for a strict file-naming SOP.
You are given representative video frames and metadata.

Metadata / source hints:
{context}

Determine, from the frames and hints:
- description: an "Action + Object" phrase for the process shown, in Title Case, e.g. "Sanding Automotive Body Panel".
- client_or_location: the client company or site name. Prefer filename/location hints. Use "Unknown" only if genuinely impossible.
- is_manual: true if a human operator performing the process is visible.
- confidence: confidence from 0.0 to 1.0 in description + client/location.
- rationale: one short sentence explaining the classification.
- flags: short uncertainty flags, e.g. ["client_unknown", "blurry_frames"].

Hard constraints: description and client_or_location MUST NOT contain underscores or any of these characters: / \\ : * ? " < > | and neither may be empty.

Respond with ONLY a JSON object with exactly these keys: description, client_or_location, is_manual, confidence, rationale, flags."""


class GeminiProvider:
    provider_id = "gemini"
    display_name = "Gemini API"
    default_model = "gemini-2.5-flash"
    cheap_model = "gemini-2.5-flash"
    accurate_model = "gemini-2.5-pro"
    env_key_names = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    def _sdk_installed(self) -> bool:
        try:
            import google.genai  # noqa: F401
        except ImportError:
            return False
        return True

    def resolve_api_key(self, api_key: str | None = None) -> str | None:
        explicit = (api_key or "").strip()
        if explicit:
            return explicit
        for name in self.env_key_names:
            value = os.environ.get(name, "").strip()
            if value:
                return value
        return None

    def status(self, config: ProviderConfig | None = None) -> ProviderStatus:
        key = self.resolve_api_key(config.api_key if config else None)
        if not self._sdk_installed():
            return ProviderStatus(
                provider_id=self.provider_id,
                display_name=self.display_name,
                available=False,
                has_key=bool(key),
                message="google-genai is not installed. Run `uv sync` or `pip install google-genai`.",
                default_model=self.default_model,
                env_key_names=self.env_key_names,
                cheap_model=self.cheap_model,
                accurate_model=self.accurate_model,
            )
        if not key:
            return ProviderStatus(
                provider_id=self.provider_id,
                display_name=self.display_name,
                available=True,
                has_key=False,
                message="Gemini SDK ready. Paste an API key or set GEMINI_API_KEY. Create one at https://aistudio.google.com/apikey",
                default_model=self.default_model,
                env_key_names=self.env_key_names,
                cheap_model=self.cheap_model,
                accurate_model=self.accurate_model,
            )
        return ProviderStatus(
            provider_id=self.provider_id,
            display_name=self.display_name,
            available=True,
            has_key=True,
            message="Gemini API ready.",
            default_model=self.default_model,
            env_key_names=self.env_key_names,
            cheap_model=self.cheap_model,
            accurate_model=self.accurate_model,
        )

    def classify(self, request: ReviewRequest, config: ProviderConfig) -> ReviewResponse:
        if not self._sdk_installed():
            raise AiReviewError(self.status(config).message, ErrorCategory.MISSING_DEPENDENCY)
        key = self.resolve_api_key(config.api_key)
        if not key:
            raise AiReviewError(
                "No Gemini API key. Paste one in the app, pass --api-key, or set GEMINI_API_KEY.",
                ErrorCategory.MISSING_KEY,
            )
        model = (config.model or "").strip() or self.default_model
        payload = self._generate_with_retry(key, model, request)
        return self._parse_response(payload)

    def _generate_with_retry(self, api_key: str, model: str, request: ReviewRequest) -> dict[str, Any]:
        last_error: Exception | None = None
        attempts = max(1, request.policy.max_retries + 1)
        for attempt in range(attempts):
            try:
                return self._generate(api_key, model, request)
            except AiReviewError:
                raise
            except Exception as exc:  # noqa: BLE001 - SDK exceptions vary by version
                last_error = exc
                category = self._categorize_exception(exc)
                if category not in {ErrorCategory.RATE_LIMIT, ErrorCategory.PROVIDER_UNAVAILABLE} or attempt == attempts - 1:
                    raise AiReviewError(self._safe_exception_message(exc, category), category) from exc
                time.sleep(min(2 ** attempt, 4))
        raise AiReviewError(str(last_error or "Provider failed"), ErrorCategory.PROVIDER_UNAVAILABLE)

    def _generate(self, api_key: str, model: str, request: ReviewRequest) -> dict[str, Any]:
        from google import genai
        from google.genai import types

        from video_reviewer.ai_review.providers.common import prompt_for

        parts: list[Any] = [types.Part.from_text(text=prompt_for(request))]
        for frame in request.frames[: request.policy.max_frames]:
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
        return data

    def _parse_response(self, data: dict[str, Any]) -> ReviewResponse:
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        flags = data.get("flags") or []
        if not isinstance(flags, list):
            flags = [str(flags)]
        return ReviewResponse(
            description=str(data.get("description", "")).strip(),
            client_or_location=str(data.get("client_or_location", "")).strip(),
            is_manual=(data.get("is_manual") is True or str(data.get("is_manual", "")).strip().lower() in {"true", "1", "yes"}),
            confidence=max(0.0, min(1.0, confidence)),
            rationale=str(data.get("rationale", "")).strip(),
            flags=[str(flag).strip() for flag in flags if str(flag).strip()],
            raw_text=str(data.get("_raw_text", "")),
        )

    def _categorize_exception(self, exc: Exception) -> ErrorCategory:
        text = f"{type(exc).__name__}: {exc}".lower()
        if any(token in text for token in ("api key", "permission", "unauth", "forbidden", "401", "403")):
            return ErrorCategory.INVALID_KEY
        if any(token in text for token in ("quota", "rate", "429", "resource_exhausted")):
            return ErrorCategory.RATE_LIMIT
        if any(token in text for token in ("500", "502", "503", "504", "timeout", "temporarily")):
            return ErrorCategory.PROVIDER_UNAVAILABLE
        return ErrorCategory.PROVIDER_UNAVAILABLE

    def _safe_exception_message(self, exc: Exception, category: ErrorCategory) -> str:
        if category == ErrorCategory.INVALID_KEY:
            return "Gemini rejected the API key. Check that it is valid and enabled for AI Studio."
        if category == ErrorCategory.RATE_LIMIT:
            return "Gemini rate limit or quota reached. Try fewer videos, wait, or use a cheaper model/preset."
        return "Gemini is temporarily unavailable. Try again later or choose another provider."
