from __future__ import annotations

import json
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
from video_reviewer.ai_review.providers.gemini import PROMPT_TEMPLATE


def prompt_for(request: ReviewRequest) -> str:
    context = {
        "source_name": request.source_name,
        "year_month": request.year_month,
        "capture_time": request.capture_time,
        "source_hints": request.source_hints,
        "frames": [
            {
                "index": idx,
                "timestamp_seconds": frame.timestamp_seconds,
                "width": frame.width,
                "height": frame.height,
                "downscaled": frame.downscaled,
            }
            for idx, frame in enumerate(request.frames)
        ],
    }
    prompt = PROMPT_TEMPLATE.format(context=json.dumps(context, indent=2, sort_keys=True))

    # Appended, never interpolated: the guide and corrections are operator-owned
    # text and a stray brace would blow up .format().
    from video_reviewer.config import build_corrections_context
    from video_reviewer.naming_guide import load_guide

    sections = [prompt, "# Naming guide (authoritative — follow it over your own instincts)", load_guide()]
    corrections = build_corrections_context()
    if corrections:
        sections.append(corrections)
    return "\n\n".join(sections)


def parse_response(data: dict[str, Any]) -> ReviewResponse:
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
        is_manual=_parse_bool(data.get("is_manual", False)),
        confidence=max(0.0, min(1.0, confidence)),
        rationale=str(data.get("rationale", "")).strip(),
        flags=[str(flag).strip() for flag in flags if str(flag).strip()],
        raw_text=str(data.get("_raw_text", "")),
    )


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


class VisionProviderBase:
    """Shared key handling and bounded retry logic for official vision SDKs."""

    provider_id: str
    display_name: str
    default_model: str
    cheap_model: str
    accurate_model: str
    env_key_names: tuple[str, ...]
    missing_sdk_message: str
    key_message: str
    ready_message: str

    def resolve_api_key(self, api_key: str | None = None) -> str | None:
        explicit = (api_key or "").strip()
        if explicit:
            return explicit
        import os

        return next((os.environ[name].strip() for name in self.env_key_names if os.environ.get(name, "").strip()), None)

    def status(self, config: ProviderConfig | None = None) -> ProviderStatus:
        key = self.resolve_api_key(config.api_key if config else None)
        if not self._sdk_installed():
            return ProviderStatus(
                provider_id=self.provider_id, display_name=self.display_name, available=False,
                has_key=bool(key), message=self.missing_sdk_message, default_model=self.default_model,
                env_key_names=self.env_key_names, cheap_model=self.cheap_model, accurate_model=self.accurate_model,
            )
        message = self.ready_message if key else self.key_message
        return ProviderStatus(
            provider_id=self.provider_id, display_name=self.display_name, available=True,
            has_key=bool(key), message=message, default_model=self.default_model,
            env_key_names=self.env_key_names, cheap_model=self.cheap_model, accurate_model=self.accurate_model,
        )

    def classify(self, request: ReviewRequest, config: ProviderConfig) -> ReviewResponse:
        return parse_response(self.generate_json(
            prompt_for(request),
            request.frames[: request.policy.max_frames],
            config,
            max_retries=request.policy.max_retries,
        ))

    def generate_json(self, prompt: str, frames, config: ProviderConfig, max_retries: int = 1) -> dict[str, Any]:
        """Send any prompt plus images and get parsed JSON back, with retries."""
        if not self._sdk_installed():
            raise AiReviewError(self.missing_sdk_message, ErrorCategory.MISSING_DEPENDENCY)
        key = self.resolve_api_key(config.api_key)
        if not key:
            raise AiReviewError(self.key_message, ErrorCategory.MISSING_KEY)
        model = (config.model or "").strip() or self.default_model
        last_error: Exception | None = None
        for attempt in range(max(1, max_retries + 1)):
            try:
                return self._generate(key, model, prompt, frames)
            except AiReviewError:
                raise
            except Exception as exc:  # SDK exception classes differ between versions.
                last_error = exc
                category = self._categorize_exception(exc)
                if category not in {ErrorCategory.RATE_LIMIT, ErrorCategory.PROVIDER_UNAVAILABLE} or attempt == max_retries:
                    raise AiReviewError(self._safe_exception_message(category, exc), category) from exc
                time.sleep(min(2**attempt, 4))
        raise AiReviewError(str(last_error or "Provider failed"), ErrorCategory.PROVIDER_UNAVAILABLE)

    def _categorize_exception(self, exc: Exception) -> ErrorCategory:
        text = f"{type(exc).__name__}: {exc}".lower()
        if any(token in text for token in ("api key", "permission", "unauth", "forbidden", "401", "403")):
            return ErrorCategory.INVALID_KEY
        if any(token in text for token in ("quota", "rate", "429", "resource_exhausted")):
            return ErrorCategory.RATE_LIMIT
        return ErrorCategory.PROVIDER_UNAVAILABLE

    def _safe_exception_message(self, category: ErrorCategory, exc: Exception) -> str:
        if category == ErrorCategory.INVALID_KEY:
            return f"{self.display_name} rejected the API key. Check that it is valid and enabled."
        if category == ErrorCategory.RATE_LIMIT:
            return f"{self.display_name} rate limit or quota reached. Try fewer videos or a cheaper preset."
        return f"{self.display_name} is temporarily unavailable. Try again later or choose another provider."
