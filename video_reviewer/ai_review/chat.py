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
decides. Tools that do need that (searching the batch) would want a bounded
action loop here instead.

Frames are sent once, not every turn. On the first turn the model also writes
frame_notes — everything it can see that bears on naming — and later turns carry
those notes as text instead of the images. The provider APIs are stateless and
neither form of context caching helps at this prompt size, so seeing once and
remembering in words is what keeps a conversation affordable. If the notes turn
out to be too thin, the model says so and the next turn re-attaches the frames.
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
EDITABLE_FIELDS = ("part", "description", "industry", "client_or_location", "year_month", "sequence")
GLOBAL_EDITABLE_FIELDS = ("part", "description", "industry", "client_or_location", "year_month")
MAX_BATCH_CONTEXT = 200

# Asked for on the turn that carries the images; replayed as text on every turn
# after, so the frames only ever cross the wire once.
NOTES_CLAUSE = """  "frame_notes" REQUIRED on this turn. Write down everything you can see in the frames that
               could matter for naming: the action, the object or surface being worked on,
               whether a person is doing the work by hand or a robot is, the environment, and
               any text or logos. Six sentences at most. You will not see these frames again —
               later questions are answered from these notes, so leave nothing out.
"""

NO_FRAMES_CLAUSE = """  "need_frames" true if the notes above are genuinely not enough to answer, else null.
               Say so rather than guessing; the frames will be attached and you can answer next turn.
"""

SYSTEM_PROMPT = """You are helping an operator name one video file correctly, on the phone with them.

{vision} Answer their question directly and briefly — \
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
  "set_batch_fields" an object with any of {global_fields}, or null.
               Use it only when the operator explicitly asks for one field to change on EVERY
               clip in this open batch. It is a proposal only: the browser shows the change and
               the operator still approves each resulting filename. Never use it for a subset.
{frames_clause}
Never claim you have renamed a file or saved the manifest — you cannot. You propose the
name and the operator approves it. (A "remember" rule is genuinely written to the guide,
so you may say you noted that.)"""


@dataclass
class ChatReply:
    message: str
    set_fields: dict[str, str] = field(default_factory=dict)
    remembered: str = ""
    frame_notes: str = ""
    need_frames: bool = False
    set_batch_fields: dict[str, str] = field(default_factory=dict)


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


def _batch_context(rows) -> str:
    """Compact full-batch inventory for explicit all-clips requests."""
    inventory = [
        {
            "clip_number": index + 1,
            "source_filename": Path(row.source_path).name,
            "year_month": row.year_month,
            "description": row.description,
            "industry": row.industry,
            "client_or_location": row.client_or_location,
            "sequence": row.sequence,
            "review_status": row.review_status,
        }
        for index, row in enumerate(rows[:MAX_BATCH_CONTEXT])
    ]
    suffix = "" if len(rows) <= MAX_BATCH_CONTEXT else f"\nOnly the first {MAX_BATCH_CONTEXT} of {len(rows)} clips are listed."
    return json.dumps({"batch_size": len(rows), "clips": inventory}, indent=2, sort_keys=True) + suffix


def _transcript(messages: list[dict]) -> str:
    lines = []
    for message in messages[-MAX_HISTORY:]:
        role = "Operator" if message.get("role") == "user" else "You"
        text = str(message.get("content", "")).strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _build_prompt(row, index: int, rows, messages: list[dict], frame_notes: str) -> str:
    from video_reviewer.config import build_corrections_context

    has_frames = not frame_notes
    sections = [
        SYSTEM_PROMPT.format(
            fields=", ".join(EDITABLE_FIELDS),
            global_fields=", ".join(GLOBAL_EDITABLE_FIELDS),
            vision=(
                "You can see sample frames from the clip."
                if has_frames
                else "You looked at this clip earlier. Your own notes on what you saw are below; "
                "the frames are not attached again."
            ),
            frames_clause=NOTES_CLAUSE if has_frames else NO_FRAMES_CLAUSE,
        ),
        "# This clip\n" + _clip_context(row, index),
        "# Open batch\n" + _batch_context(rows),
    ]
    if frame_notes:
        sections.append("# What you saw in the frames\n" + frame_notes)
    sections.append("# Naming guide (authoritative)\n" + load_guide())
    corrections = build_corrections_context()
    if corrections:
        sections.append(corrections)
    # The conversation goes last: everything above it is identical turn to turn,
    # which is the only shape a provider prefix cache could ever reuse.
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


def _clean_batch_fields(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        name: str(raw[name]).strip()
        for name in GLOBAL_EDITABLE_FIELDS
        if raw.get(name) is not None and str(raw[name]).strip()
    }


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
    frame_notes: str = "",
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
    # Notes in hand means the model has already looked; send words, not pictures.
    frames = [] if frame_notes else _load_frames(row, ReviewPolicy(max_frames=max_frames))[:max_frames]

    data = provider.generate_json(
        _build_prompt(row, index, rows, messages, frame_notes), frames, config, max_retries=1,
    )

    message = str(data.get("message", "")).strip()
    if not message:
        raise AiReviewError("The model replied with nothing. Try asking again.", ErrorCategory.MALFORMED_RESPONSE)

    remembered = ""
    rule = data.get("remember")
    if isinstance(rule, str) and rule.strip():
        remembered = append_rule(rule)

    notes = data.get("frame_notes")
    return ChatReply(
        message=message,
        set_fields=_clean_fields(data.get("set_fields")),
        remembered=remembered,
        frame_notes=str(notes).strip() if isinstance(notes, str) else frame_notes,
        need_frames=bool(data.get("need_frames")) and bool(frame_notes),
        set_batch_fields=_clean_batch_fields(data.get("set_batch_fields")),
    )
