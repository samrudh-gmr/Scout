from __future__ import annotations

from typing import Protocol

from video_reviewer.ai_review.models import ProviderConfig, ProviderStatus, ReviewRequest, ReviewResponse


class AiReviewProvider(Protocol):
    provider_id: str
    display_name: str
    default_model: str
    env_key_names: tuple[str, ...]
    cheap_model: str
    accurate_model: str

    def status(self, config: ProviderConfig | None = None) -> ProviderStatus:
        ...

    def classify(self, request: ReviewRequest, config: ProviderConfig) -> ReviewResponse:
        ...
