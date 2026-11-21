"""What the operator tells Scout about a batch before any model looks at it.

Part choices are a short candidate list ("RV Front Cap, Truck Hood") typed on
Prepare. They narrow what the model may call the part; they never force one.
A clip whose part is not on the list goes to a person instead.
"""
from __future__ import annotations

import re
from typing import Any

from video_reviewer.sop import clean_field, validate_field

MAX_PART_CHOICES = 12
_SEPARATORS = re.compile(r"[,;\n]+")


def _key(value: str) -> str:
    return clean_field(value).casefold()


def parse_part_choices(value: Any) -> list[str]:
    """Split operator input into clean, unique part names, in the order given.

    Raises ValueError naming the first entry that could not be a filename field.
    """
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else _SEPARATORS.split(str(value))
    choices: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = clean_field(str(item))
        if not text or _key(text) in seen:
            continue
        try:
            validate_field("part", text)
        except ValueError as exc:
            raise ValueError(f'Part choice "{text}" cannot be used in a filename: {exc}') from exc
        seen.add(_key(text))
        choices.append(text)
    if len(choices) > MAX_PART_CHOICES:
        raise ValueError(f"Too many part choices ({len(choices)}). Keep it to {MAX_PART_CHOICES} or fewer.")
    return choices


def part_choices_from_hints(hints: Any) -> list[str]:
    """The choices saved on a manifest row. Old or hand-edited rows read as none."""
    raw = hints.get("part_choices") if isinstance(hints, dict) else None
    if not isinstance(raw, list):
        return []
    choices: list[str] = []
    for item in raw:
        if isinstance(item, str) and clean_field(item) and clean_field(item) not in choices:
            choices.append(clean_field(item))
    return choices


def match_part_choice(part: str, choices: list[str]) -> str | None:
    """The operator's spelling of ``part`` if it is one of the choices, else None."""
    wanted = _key(part)
    if not wanted:
        return None
    return next((choice for choice in choices if _key(choice) == wanted), None)

