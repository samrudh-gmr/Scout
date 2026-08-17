from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ErrorCategory(StrEnum):
    MISSING_KEY = "missing_key"
    MISSING_DEPENDENCY = "missing_dependency"
    INVALID_KEY = "invalid_key"
    RATE_LIMIT = "rate_limit"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    VALIDATION = "validation"
    NO_FRAMES = "no_frames"
    NOT_FOUND = "not_found"


class AiReviewError(RuntimeError):
    """Typed error surfaced to CLI/GUI without exposing secrets or raw payloads."""

    def __init__(self, message: str, category: ErrorCategory = ErrorCategory.PROVIDER_UNAVAILABLE):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str = "gemini"
    model: str = ""
    api_key: str | None = None


@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str
    display_name: str
    available: bool
    has_key: bool
    message: str
    default_model: str
    env_key_names: tuple[str, ...] = ()
    cheap_model: str = ""
    accurate_model: str = ""


@dataclass(frozen=True)
class ReviewPolicy:
    """Cost/quality controls for AI review."""

    max_frames: int = 12
    confidence_threshold: float = 0.78
    allow_unknown_client: bool = False
    allow_second_pass: bool = True
    second_pass_threshold: float = 0.68
    max_retries: int = 2

    @classmethod
    def from_preset(cls, preset: str) -> "ReviewPolicy":
        normalized = (preset or "balanced").strip().lower()
        if normalized in {"fast", "cheap", "cheapest"}:
            return cls(max_frames=6, confidence_threshold=0.80, allow_second_pass=False, max_retries=1)
        if normalized in {"accurate", "best", "most-accurate"}:
            return cls(max_frames=16, confidence_threshold=0.75, allow_second_pass=True, second_pass_threshold=0.72, max_retries=2)
        return cls()


@dataclass(frozen=True)
class FramePayload:
    path: Path
    data: bytes
    mime_type: str
    timestamp_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    downscaled: bool = False


@dataclass(frozen=True)
class ReviewRequest:
    source_name: str
    year_month: str
    capture_time: str
    source_hints: dict[str, Any]
    frames: list[FramePayload]
    policy: ReviewPolicy = field(default_factory=ReviewPolicy)


@dataclass(frozen=True)
class ReviewResponse:
    description: str
    client_or_location: str
    is_manual: bool
    confidence: float
    rationale: str = ""
    flags: list[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class RowResult:
    index: int
    source_name: str
    ok: bool
    status: str
    proposed_name: str = ""
    description: str = ""
    client_or_location: str = ""
    confidence: float = 0.0
    response: str = ""
    error: str = ""
    error_category: str = ""
