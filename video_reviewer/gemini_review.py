"""Backwards-compatible Gemini API review wrapper.

New code should use :mod:`video_reviewer.ai_review.service`, which keeps the
review pipeline provider-neutral. This module remains so older scripts using
``video-renamer gemini-review`` continue to work.
"""
from __future__ import annotations

from pathlib import Path

from video_reviewer.ai_review.models import AiReviewError as GeminiError, RowResult
from video_reviewer.ai_review.service import pending_indices, provider_status, review_rows_with_ai

DEFAULT_MODEL = "gemini-2.5-flash"
CONFIDENCE_THRESHOLD = 0.78


def _sdk_installed() -> bool:
    from video_reviewer.ai_review.providers.gemini import GeminiProvider
    return GeminiProvider()._sdk_installed()


def resolve_api_key(api_key: str | None = None) -> str | None:
    from video_reviewer.ai_review.providers.gemini import GeminiProvider
    return GeminiProvider().resolve_api_key(api_key)


def gemini_available(api_key: str | None = None) -> tuple[bool, bool, str]:
    status = provider_status("gemini", api_key=api_key)
    return status.available, status.has_key, status.message


def review_rows(
    manifest_path: Path,
    indices: list[int],
    *,
    model: str | None = None,
    api_key: str | None = None,
    on_progress=None,
) -> list[RowResult]:
    return review_rows_with_ai(
        manifest_path,
        indices,
        provider_id="gemini",
        model=model,
        api_key=api_key,
        on_progress=on_progress,
    )
