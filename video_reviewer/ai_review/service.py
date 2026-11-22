from __future__ import annotations

import json
import time

from dataclasses import asdict
from pathlib import Path

from video_reviewer.ai_review.decide import Decision, decide
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
from video_reviewer.ai_review.providers.codex_proxy import CodexProxyProvider
from video_reviewer.ai_review.trace import decision_context, write_review_trace
from video_reviewer.manifest import manifest_transaction, REVIEW_APPROVED, REVIEW_BLOCKED, REVIEW_NEEDS_REVIEW, REVIEW_PENDING, read_manifest_csv, write_manifest_csv
from video_reviewer.media import extract_sample_frames, create_frame_dir
from video_reviewer.run_context import part_choices_from_hints
from video_reviewer.sop import build_proposed_name
from video_reviewer.workflow import resequence_and_rename

_PROVIDERS = {
    "gemini": GeminiProvider(),
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "codex-proxy": CodexProxyProvider(),
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
    thinking_level: str | None = None,
    policy: ReviewPolicy | None = None,
    on_progress=None,
) -> list[RowResult]:
    policy = policy or ReviewPolicy()
    provider = get_provider(provider_id)
    config = ProviderConfig(
        provider_id=provider.provider_id, model=model or "", api_key=api_key, thinking_level=thinking_level or "",
    )
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

    started = time.monotonic()
    request = response = decision = None
    context = _decision_context(row, policy)
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
            # _apply_response only returns normally once build_proposed_name has
            # already succeeded once (a raise there sets BLOCKED and propagates
            # as AiReviewError instead), so review_status here is always
            # REVIEW_APPROVED or REVIEW_NEEDS_REVIEW — never BLOCKED.
            decision = _apply_response(row, response, policy)
            # _apply_response named this row alone, off whatever placeholder
            # sequence it already had (always "001" fresh from prepare) — dedupe
            # across the whole batch now that this row has real fields. This can
            # renumber any OTHER row sharing this row's name group too, so use
            # the shared helper that keeps every affected row's proposed_name in
            # sync, not just this one.
            resequence_and_rename(rows)
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
    write_review_trace(
        manifest_path,
        secrets=(config.api_key,),
        source_name=source_name,
        provider=getattr(provider, "provider_id", config.provider_id),
        model=(config.model or "").strip() or getattr(provider, "default_model", ""),
        request=request,
        response=response,
        context=context,
        decision=decision,
        wall_seconds=time.monotonic() - started,
        status=row_status,
        proposed_name=getattr(row, "proposed_name", "") if row is not None else "",
        error_category=category,
        error=error,
    )
    return RowResult(
        index=index,
        source_name=source_name,
        ok=(row_status == REVIEW_APPROVED and not error),
        status=row_status,
        proposed_name=getattr(row, "proposed_name", ""),
        part=getattr(row, "part", ""),
        description=getattr(row, "description", ""),
        industry=getattr(row, "industry", ""),
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
        frames=frames,
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
    if len(existing) > policy.max_frames > 1:
        # Keep both ends of the sampled timeline, not just its beginning.
        selected = [
            existing[round(index * (len(existing) - 1) / (policy.max_frames - 1))]
            for index in range(policy.max_frames)
        ]
    else:
        selected = existing[: policy.max_frames]
    metadata = _frame_metadata(row)
    frames: list[FramePayload] = []
    for path in selected:
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


def _decision_context(row, policy: ReviewPolicy) -> dict:
    hints = _parse_hints(row.source_hints)
    return decision_context(
        batch_client_override=str(hints.get("batch_client_override", "")).strip(),
        part_choices=part_choices_from_hints(hints),
        policy=policy,
    )


def _apply_response(row, response: ReviewResponse, policy: ReviewPolicy) -> Decision:
    if not response.description or not response.client_or_location:
        raise AiReviewError("AI response was missing description or client/location.", ErrorCategory.MALFORMED_RESPONSE)
    hints = _parse_hints(row.source_hints)
    batch_client_override = str(hints.get("batch_client_override", "")).strip()
    decision = decide(
        response,
        batch_client_override=batch_client_override,
        policy=policy,
        part_choices=part_choices_from_hints(hints),
    )
    row.description = decision.description
    row.part = decision.part
    row.industry = decision.industry
    row.client_or_location = decision.client_or_location
    row.ai_confidence = f"{decision.confidence:.2f}"
    row.ai_rationale = response.rationale.strip()
    row.ai_flags = "; ".join(decision.flags)
    try:
        row.proposed_name = build_proposed_name(row)
    except ValueError as exc:
        row.review_status = REVIEW_BLOCKED
        raise AiReviewError(str(exc), ErrorCategory.VALIDATION) from exc
    row.review_status = REVIEW_APPROVED if decision.auto_approve else REVIEW_NEEDS_REVIEW
    return decision


def _merge_flags(existing: str, flag: str) -> str:
    flags = [part.strip() for part in (existing or "").split(";") if part.strip()]
    if flag not in flags:
        flags.append(flag)
    return "; ".join(flags)


def row_results_to_dicts(results: list[RowResult]) -> list[dict]:
    return [asdict(result) for result in results]
