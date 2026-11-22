"""Append-only review trace: one JSON line per reviewed clip, next to the manifest.

A line holds what is needed to diagnose a wrong name, replay the deterministic
``decide`` step and compute cost: the prompt, the frame names sent, the raw
reply, the parsed response, the decision context and result, token usage and
timing. It never holds API keys, request headers or image bytes.

Writing a trace is best effort. ``write_review_trace`` swallows every error so
a full disk or a bad path can never change a review result.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from video_reviewer.ai_review.decide import Decision, decide
from video_reviewer.ai_review.models import ReviewPolicy, ReviewRequest, ReviewResponse

TRACE_VERSION = 1
TRACE_SUFFIX = ".trace.jsonl"
_REDACTED = "[redacted]"


def trace_path_for(manifest_path: Path) -> Path:
    """``<dir>/<manifest stem>.trace.jsonl``, beside the manifest."""
    manifest_path = Path(manifest_path)
    return manifest_path.with_name(f"{manifest_path.stem}{TRACE_SUFFIX}")


def decision_context(*, batch_client_override: str, part_choices: list[str], policy: ReviewPolicy) -> dict[str, Any]:
    """Everything besides the response that ``decide`` reads."""
    return {
        "batch_client_override": batch_client_override,
        "part_choices": list(part_choices),
        "policy": asdict(policy),
    }


def build_trace_line(
    *,
    source_name: str,
    provider: str,
    model: str,
    request: ReviewRequest | None,
    response: ReviewResponse | None,
    context: dict[str, Any] | None,
    decision: Decision | None,
    wall_seconds: float | None,
    status: str = "",
    proposed_name: str = "",
    error_category: str = "",
    error: str = "",
) -> dict[str, Any]:
    prompt = _prompt(request)
    parsed = None
    if response is not None:
        parsed = asdict(response)
        # Kept once, at the top level, as the reply the model actually sent.
        parsed.pop("raw_text", None)
        parsed.pop("usage", None)
    return {
        "trace_version": TRACE_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_name": source_name,
        "provider": provider,
        "model": model,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt is not None else None,
        "prompt": prompt,
        "frames": [Path(frame.path).name for frame in request.frames] if request is not None else [],
        "raw_reply": response.raw_text if response is not None else None,
        "response": parsed,
        "context": context,
        "decision": asdict(decision) if decision is not None else None,
        "usage": dict(response.usage) if response is not None and response.usage is not None else None,
        "wall_seconds": round(wall_seconds, 3) if wall_seconds is not None else None,
        "status": status,
        "proposed_name": proposed_name,
        "error_category": error_category,
        "error": error,
    }


def write_review_trace(manifest_path: Path, *, secrets: tuple[str | None, ...] = (), **fields: Any) -> bool:
    """Append one trace line. Returns False instead of raising on any failure.

    ``secrets`` are values (API keys) scrubbed from the line if they appear
    anywhere in it; nothing in a trace should contain one, this is a backstop.
    """
    try:
        line = json.dumps(build_trace_line(**fields), ensure_ascii=False, sort_keys=True)
        for secret in secrets:
            if secret and len(secret) >= 4:
                line = line.replace(secret, _REDACTED)
        with trace_path_for(manifest_path).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True
    except Exception:  # noqa: BLE001 - a trace must never change a review result
        return False


def replay_decision(line: dict[str, Any] | str) -> Decision:
    """Re-run ``decide`` on a trace line's recorded response and context."""
    record = json.loads(line) if isinstance(line, str) else line
    parsed = dict(record["response"])
    response = ReviewResponse(**{**parsed, "flags": list(parsed.get("flags") or []), "raw_text": record.get("raw_reply") or ""})
    context = record.get("context") or {}
    return decide(
        response,
        batch_client_override=context.get("batch_client_override", ""),
        policy=ReviewPolicy(**(context.get("policy") or {})),
        part_choices=list(context.get("part_choices") or []),
    )


def _prompt(request: ReviewRequest | None) -> str | None:
    if request is None:
        return None
    from video_reviewer.ai_review.prompt import prompt_for

    return prompt_for(request)
