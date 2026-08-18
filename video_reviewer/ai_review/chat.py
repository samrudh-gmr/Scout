"""Ask the model about one clip, in conversation.

The harness is deliberately small. The model sees the clip's frames, its current
field values, the naming guide and past corrections, and the conversation so
far. It answers with JSON that may carry two actions:

  set_fields  proposed name fields — returned to the browser, which drops them
              into the form. The operator still presses Approve, so nothing
              reaches the manifest without a human.
  remember    one rule appended to the naming guide, so a correction made in
              chat also steers every future batch.

Both are single-shot: neither needs the model to observe a result before it
decides. Tools that do need that (re-sampling frames, searching the batch)
would want a bounded action loop here instead.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from video_reviewer.ai_review.models import (
    AiReviewError,
    ErrorCategory,
    ProviderConfig,
    ReviewPolicy,
)
from video_reviewer.ai_review.service import get_provider
from video_reviewer.manifest import read_manifest_csv
from video_reviewer.naming_guide import GUIDE_PATH, load_guide

# Frames dominate the cost and are re-sent every turn, so the chat budget is
# deliberately smaller than the review preset's.
CHAT_FRAMES = 5
MAX_HISTORY = 12
EDITABLE_FIELDS = ("description", "client_or_location", "year_month", "sequence")

SYSTEM_PROMPT = """You are helping an operator name one video file correctly, on the phone with them.

You can see sample frames from the clip. Answer their question directly and briefly — \
two or three sentences, plain language, no preamble and no bullet lists unless they ask for one.

Reply with ONLY a JSON object with these keys:

  "message"    what you say to the operator. Required.
  "set_fields" an object with any of {fields}, or null.
               Include it whenever you are proposing a change to the name — the operator
               sees your values appear in the form and decides whether to keep them.
               Send only the fields you are changing. Follow the naming guide exactly.
  "remember"   one short rule to add to the naming guide, or null.
               Use it only when the operator tells you something that should hold for
               FUTURE clips too — a client's correct spelling, a term to prefer or avoid.
               Do not use it for a fact about this one clip.

Never claim you have renamed a file or saved the manifest — you cannot. You propose the
name and the operator approves it. (A "remember" rule is genuinely written to the guide,
so you may say you noted that.)"""


@dataclass
class ChatReply:
    message: str
    set_fields: dict[str, str] = field(default_factory=dict)
    remembered: str = ""


def _clip_context(row, index: int) -> str:
    return json.dumps(
        {
            "clip_number": index + 1,
            "source_filename": Path(row.source_path).name,
            "current_fields": {name: getattr(row, name, "") for name in EDITABLE_FIELDS},
            "proposed_name": row.proposed_name,
            "review_status": row.review_status,
            "model_confidence": row.ai_confidence,
            "model_rationale": row.ai_rationale,
            "model_flags": row.ai_flags,
        },
        indent=2,
        sort_keys=True,
    )


def _transcript(messages: list[dict]) -> str:
    lines = []
    for message in messages[-MAX_HISTORY:]:
        role = "Operator" if message.get("role") == "user" else "You"
        text = str(message.get("content", "")).strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _build_prompt(row, index: int, messages: list[dict]) -> str:
    from video_reviewer.config import build_corrections_context

    sections = [
        SYSTEM_PROMPT.format(fields=", ".join(EDITABLE_FIELDS)),
        "# This clip\n" + _clip_context(row, index),
        "# Naming guide (authoritative)\n" + load_guide(),
    ]
    corrections = build_corrections_context()
    if corrections:
        sections.append(corrections)
    sections.append("# Conversation\n" + _transcript(messages))
    return "\n\n".join(sections)


def _clean_fields(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    fields = {}
    for name in EDITABLE_FIELDS:
        value = raw.get(name)
        if value is None:
            continue
        text = str(value).strip()
        # A field the model "changed" to its current value is noise, but an
        # empty string is a real instruction to clear it, so keep both apart.
        if text or name in raw:
            fields[name] = text
    return fields


def append_rule(rule: str) -> str:
    """Add one operator rule to the naming guide, under its own heading."""
    rule = " ".join(rule.split()).strip().rstrip(".")
    if not rule:
        return ""
    guide = load_guide()
    heading = "## Operator notes"
    line = f"- {rule}"
    if line in guide:
        return rule
    if heading in guide:
        head, _, tail = guide.partition(heading)
        guide = f"{head}{heading}{tail.rstrip()}\n{line}\n"
    else:
        guide = f"{guide.rstrip()}\n\n{heading}\n\nAdded from chat.\n\n{line}\n"
    GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUIDE_PATH.write_text(guide, encoding="utf-8")
    return rule


def chat_about_row(
    manifest_path: Path,
    index: int,
    messages: list[dict],
    *,
    provider_id: str = "gemini",
    model: str | None = None,
    api_key: str | None = None,
    max_frames: int = CHAT_FRAMES,
) -> ChatReply:
    from video_reviewer.ai_review.service import _load_frames

    rows = read_manifest_csv(manifest_path)
    if index < 0 or index >= len(rows):
        raise AiReviewError("That clip is no longer in the batch.", ErrorCategory.NOT_FOUND)
    row = rows[index]

    if not any(str(m.get("content", "")).strip() for m in messages):
        raise AiReviewError("Ask a question first.", ErrorCategory.VALIDATION)

    provider = get_provider(provider_id)
    config = ProviderConfig(provider_id=provider.provider_id, model=model or "", api_key=api_key)
    frames = _load_frames(row, ReviewPolicy(max_frames=max_frames))[:max_frames]

    data = provider.generate_json(_build_prompt(row, index, messages), frames, config, max_retries=1)

    message = str(data.get("message", "")).strip()
    if not message:
        raise AiReviewError("The model replied with nothing. Try asking again.", ErrorCategory.MALFORMED_RESPONSE)

    remembered = ""
    rule = data.get("remember")
    if isinstance(rule, str) and rule.strip():
        remembered = append_rule(rule)

    return ChatReply(message=message, set_fields=_clean_fields(data.get("set_fields")), remembered=remembered)
