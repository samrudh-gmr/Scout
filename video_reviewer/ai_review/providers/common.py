from __future__ import annotations

import os
import re
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
from video_reviewer.ai_review.prompt import prompt_for

__all__ = ["VisionProviderBase", "parse_response", "prompt_for"]

_KEY_TOKENS = ("api key", "api_key", "permission", "unauth", "forbidden")
# A spent prepaid balance arrives as RESOURCE_EXHAUSTED too, so it is checked first.
_CREDIT_TOKENS = ("credits are depleted", "prepayment credit", "insufficient credit", "billing account")
_RATE_TOKENS = ("rate limit", "rate_limit", "ratelimit", "quota", "resource_exhausted", "too many requests")
_REQUEST_TOKENS = ("not found", "not_found", "invalid_argument", "invalid argument", "bad request", "unsupported")
_RETRYABLE = {ErrorCategory.RATE_LIMIT, ErrorCategory.PROVIDER_UNAVAILABLE}


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
        part=str(data.get("part", "")).strip(),
        industry=str(data.get("industry", "")).strip(),
        rationale=str(data.get("rationale", "")).strip(),
        flags=[str(flag).strip() for flag in flags if str(flag).strip()],
        raw_text=str(data.get("_raw_text", "")),
        scene=str(data.get("scene", "") or "").strip(),
        usage=data.get("_usage") if isinstance(data.get("_usage"), dict) else None,
    )


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _http_status(exc: Exception) -> int | None:
    """The HTTP status an SDK error carries, or a leading one in its message."""
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool) and 400 <= value <= 599:
            return value
    matched = re.match(r"\s*(?:error code:?\s*)?([45]\d\d)\b", str(exc), re.IGNORECASE)
    return int(matched.group(1)) if matched else None


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
        attempts = max(1, max_retries + 1)
        for attempt in range(attempts):
            try:
                return self._generate(key, model, prompt, frames)
            except AiReviewError:
                raise
            except Exception as exc:  # SDK exception classes differ between versions.
                category = self._categorize_exception(exc)
                # Only a busy or throttled provider is worth asking again. A bad
                # key, an unknown model or a malformed request fails the same way
                # every time, so retrying only delays the real message.
                if category not in _RETRYABLE or attempt == attempts - 1:
                    raise AiReviewError(self._safe_exception_message(category, exc, model), category) from exc
                time.sleep(min(2**attempt, 4))
        raise AiReviewError(self._safe_exception_message(ErrorCategory.PROVIDER_UNAVAILABLE, None, model))

    def _categorize_exception(self, exc: Exception) -> ErrorCategory:
        code = _http_status(exc)
        text = f"{type(exc).__name__}: {exc}".lower()
        if code in (401, 403) or any(token in text for token in _KEY_TOKENS):
            return ErrorCategory.INVALID_KEY
        if code == 402 or any(token in text for token in _CREDIT_TOKENS):
            return ErrorCategory.OUT_OF_CREDIT
        if code == 429 or any(token in text for token in _RATE_TOKENS):
            return ErrorCategory.RATE_LIMIT
        if (code is not None and 400 <= code < 500 and code != 408) or any(token in text for token in _REQUEST_TOKENS):
            return ErrorCategory.INVALID_REQUEST
        return ErrorCategory.PROVIDER_UNAVAILABLE

    def _safe_exception_message(self, category: ErrorCategory, exc: Exception | None, model: str = "") -> str:
        if category == ErrorCategory.INVALID_KEY:
            return f"{self.display_name} rejected the API key. Check that it is valid and enabled."
        if category == ErrorCategory.OUT_OF_CREDIT:
            return f"{self.display_name} account is out of prepaid credit. Add credit in the provider's billing page, then run the review again."
        if category == ErrorCategory.RATE_LIMIT:
            return f"{self.display_name} rate limit or quota reached. Try fewer videos or a cheaper preset."
        if category == ErrorCategory.INVALID_REQUEST:
            return (
                f"{self.display_name} rejected the request for model '{model}'. "
                "The model may be misspelled, retired, or unavailable to this key. Pick another model and try again."
            )
        return f"{self.display_name} is temporarily unavailable. Try again later or choose another provider."
