from __future__ import annotations

import json

from dataclasses import asdict
from pathlib import Path

from video_reviewer.ai_review.models import (
    AiReviewError,
    ErrorCategory,
    FramePayload,
    ProviderConfig,
    ProviderStatus,
    ReviewPolicy,
    ReviewRequest,
    ReviewResponse,
    RowResult,
)
from video_reviewer.ai_review.providers.gemini import GeminiProvider
from video_reviewer.ai_review.providers.anthropic import AnthropicProvider
from video_reviewer.ai_review.providers.openai import OpenAIProvider
from video_reviewer.manifest import manifest_transaction, REVIEW_APPROVED, REVIEW_BLOCKED, REVIEW_NEEDS_REVIEW, REVIEW_PENDING, read_manifest_csv, write_manifest_csv
from video_reviewer.media import extract_sample_frames, create_frame_dir
from video_reviewer.sop import build_proposed_name

_PROVIDERS = {
    "gemini": GeminiProvider(),
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
}



def available_providers() -> list[dict[str, str]]:
    return [
        {
            "id": provider.provider_id,
            "display_name": provider.display_name,
            "default_model": provider.default_model,
            "cheap_model": getattr(provider, "cheap_model", provider.default_model),
            "accurate_model": getattr(provider, "accurate_model", provider.default_model),
            "env_key_names": list(getattr(provider, "env_key_names", ())),
        }
        for provider in _PROVIDERS.values()
    ]


def get_provider(provider_id: str):
    normalized = (provider_id or "gemini").strip().lower()
    try:
        return _PROVIDERS[normalized]
    except KeyError as exc:
        raise AiReviewError(
            f"Unknown AI provider '{provider_id}'. Available providers: {', '.join(sorted(_PROVIDERS))}.",
            ErrorCategory.NOT_FOUND,
        ) from exc


def provider_status(provider_id: str = "gemini", *, api_key: str | None = None, model: str | None = None) -> ProviderStatus:
    provider = get_provider(provider_id)
    return provider.status(ProviderConfig(provider_id=provider.provider_id, model=model or "", api_key=api_key))


def pending_indices(manifest_path: Path) -> list[int]:
    rows = read_manifest_csv(manifest_path)
    return [i for i, row in enumerate(rows) if row.review_status in (REVIEW_PENDING, REVIEW_BLOCKED)]


def estimate_review_cost(row_count: int, policy: ReviewPolicy) -> dict[str, int | str]:
    frames = max(0, row_count) * policy.max_frames
    if frames <= 60:
        tier = "low"
    elif frames <= 250:
        tier = "medium"
    else:
        tier = "high"
    return {"videos": row_count, "frames_per_video": policy.max_frames, "total_frames": frames, "cost_tier": tier}


def review_rows_with_ai(
    manifest_path: Path,
    indices: list[int],
    *,
    provider_id: str = "gemini",
    model: str | None = None,
    api_key: str | None = None,
    policy: ReviewPolicy | None = None,
    on_progress=None,
) -> list[RowResult]:
    policy = policy or ReviewPolicy()
    provider = get_provider(provider_id)
    config = ProviderConfig(provider_id=provider.provider_id, model=model or "", api_key=api_key)
    status = provider.status(config)
    if not status.available:
        raise AiReviewError(status.message, ErrorCategory.MISSING_DEPENDENCY)
    if not status.has_key:
        raise AiReviewError(status.message, ErrorCategory.MISSING_KEY)

    results: list[RowResult] = []
    for index in indices:
        result = _review_one_row(manifest_path, index, provider, config, policy)
        results.append(result)
        if on_progress is not None:
            on_progress(result)
    return results


def _review_one_row(manifest_path: Path, index: int, provider, config: ProviderConfig, policy: ReviewPolicy) -> RowResult:
    with manifest_transaction(manifest_path):
        rows = read_manifest_csv(manifest_path)
        if index < 0 or index >= len(rows):
            return RowResult(index=index, source_name=str(index), ok=False, status="missing", error="row index out of range", error_category=ErrorCategory.NOT_FOUND.value)
        row = rows[index]
        source_identity = str(Path(row.source_path).resolve())
        source_name = Path(row.source_path).name

    try:
        request = _build_request(row, policy)
        response = provider.classify(request, config)
        with manifest_transaction(manifest_path):
            rows = read_manifest_csv(manifest_path)
            matching = [candidate for candidate in rows if str(Path(candidate.source_path).resolve()) == source_identity]
            if len(matching) != 1:
                raise AiReviewError(
                    "The video list changed while AI review was running. Refresh and review this row again.",
                    ErrorCategory.VALIDATION,
                )
            row = matching[0]
            _apply_response(row, response, policy)
            write_manifest_csv(manifest_path, rows)
        error = ""
        category = ""
    except AiReviewError as exc:
        error = str(exc)
        category = exc.category.value
        with manifest_transaction(manifest_path):
            rows = read_manifest_csv(manifest_path)
            matching = [candidate for candidate in rows if str(Path(candidate.source_path).resolve()) == source_identity]
            if len(matching) == 1:
                row = matching[0]
                if exc.category in {ErrorCategory.VALIDATION, ErrorCategory.MALFORMED_RESPONSE, ErrorCategory.NO_FRAMES}:
                    row.review_status = REVIEW_BLOCKED if exc.category == ErrorCategory.VALIDATION else REVIEW_NEEDS_REVIEW
                    row.ai_flags = _merge_flags(row.ai_flags, exc.category.value)
                    row.ai_rationale = error
                    write_manifest_csv(manifest_path, rows)
                source_name = Path(row.source_path).name
            else:
                row = None
    row_status = getattr(row, "review_status", "missing") if 'row' in locals() and row is not None else "missing"
    return RowResult(
        index=index,
        source_name=source_name,
        ok=(row_status == REVIEW_APPROVED and not error),
        status=row_status,
        proposed_name=getattr(row, "proposed_name", ""),
        description=getattr(row, "description", ""),
        client_or_location=getattr(row, "client_or_location", ""),
        confidence=float(getattr(row, "ai_confidence", "") or 0.0),
        response=getattr(row, "ai_rationale", ""),
        error=error,
        error_category=category,
    )


def _build_request(row, policy: ReviewPolicy) -> ReviewRequest:
    hints = _parse_hints(row.source_hints)
    frames = _load_frames(row, policy)
    if not frames:
        raise AiReviewError("No sample frames found. Run prepare first, or regenerate frames.", ErrorCategory.NO_FRAMES)
    return ReviewRequest(
        source_name=Path(row.source_path).name,
        year_month=row.year_month,
        capture_time=row.capture_time,
        source_hints=hints,
        frames=frames[: policy.max_frames],
        policy=policy,
    )


def _parse_hints(raw: str) -> dict:
    try:
        payload = json.loads(raw or "{}")
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _frame_metadata(row) -> dict[str, dict]:
    try:
        direct = json.loads(getattr(row, "ai_frame_metadata", "") or "{}")
    except json.JSONDecodeError:
        direct = {}
    if isinstance(direct, dict) and direct:
        return direct
    hints = _parse_hints(row.source_hints)
    metadata = hints.get("sample_frames", {})
    return metadata if isinstance(metadata, dict) else {}


def _load_frames(row, policy: ReviewPolicy) -> list[FramePayload]:
    frame_paths = [Path(raw) for raw in (row.sample_frames or "").split("|") if raw]
    existing = [path for path in frame_paths if path.exists()]
    if not existing and Path(row.source_path).exists():
        # Last-resort recovery: regenerate exact source frames for this row.
        hints = _parse_hints(row.source_hints)
        try:
            duration = float(hints.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        frame_dir = create_frame_dir(Path(row.proxy_path).parent if row.proxy_path else Path(row.source_path).parent / "tmp", Path(row.source_path))
        existing = extract_sample_frames(Path(row.source_path), frame_dir, min(policy.max_frames, 12), duration=duration)
    metadata = _frame_metadata(row)
    frames: list[FramePayload] = []
    for path in existing[: policy.max_frames]:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        info = metadata.get(path.name, {}) if isinstance(metadata, dict) else {}
        frames.append(
            FramePayload(
                path=path,
                data=path.read_bytes(),
                mime_type=mime,
                timestamp_seconds=_optional_float(info.get("timestamp_seconds")),
                width=_optional_int(info.get("width")),
                height=_optional_int(info.get("height")),
                downscaled=bool(info.get("downscaled", False)),
            )
        )
    return frames


def _optional_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_response(row, response: ReviewResponse, policy: ReviewPolicy) -> None:
    if not response.description or not response.client_or_location:
        raise AiReviewError("AI response was missing description or client/location.", ErrorCategory.MALFORMED_RESPONSE)
    row.description = response.description.strip()
    row.client_or_location = response.client_or_location.strip()
    confidence = max(0.0, min(1.0, float(response.confidence)))
    row.ai_confidence = f"{confidence:.2f}"
    row.ai_rationale = response.rationale.strip()
    row.ai_flags = "; ".join(response.flags)
    try:
        row.proposed_name = build_proposed_name(row)
    except ValueError as exc:
        row.review_status = REVIEW_BLOCKED
        raise AiReviewError(str(exc), ErrorCategory.VALIDATION) from exc
    serious_flags = {flag.lower() for flag in response.flags}
    unknown_client = row.client_or_location.strip().lower() == "unknown"
    can_auto_approve = (
        confidence >= policy.confidence_threshold
        and not (unknown_client and not policy.allow_unknown_client)
        and not any(flag in serious_flags for flag in {"client_unknown", "unknown_client", "unsafe", "invalid"})
    )
    row.review_status = REVIEW_APPROVED if can_auto_approve else REVIEW_NEEDS_REVIEW


def _merge_flags(existing: str, flag: str) -> str:
    flags = [part.strip() for part in (existing or "").split(";") if part.strip()]
    if flag not in flags:
        flags.append(flag)
    return "; ".join(flags)


def row_results_to_dicts(results: list[RowResult]) -> list[dict]:
    return [asdict(result) for result in results]
