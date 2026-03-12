from __future__ import annotations

import json
from pathlib import Path


_CONFIG_DIR = Path.home() / ".video-renamer"
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_CORRECTIONS_FILE = _CONFIG_DIR / "corrections.json"
_MAX_CORRECTIONS = 50


def read_config() -> dict[str, str]:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_config(data: dict[str, str]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_corrections() -> list[dict[str, str]]:
    if _CORRECTIONS_FILE.exists():
        try:
            return json.loads(_CORRECTIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_correction(source_name: str, ai_fields: dict[str, str], corrected_fields: dict[str, str]) -> None:
    corrections = load_corrections()
    corrections.append({
        "source_name": source_name,
        "ai_description": ai_fields.get("description", ""),
        "ai_client_or_location": ai_fields.get("client_or_location", ""),
        "corrected_description": corrected_fields.get("description", ""),
        "corrected_client_or_location": corrected_fields.get("client_or_location", ""),
    })
    if len(corrections) > _MAX_CORRECTIONS:
        corrections = corrections[-_MAX_CORRECTIONS:]
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CORRECTIONS_FILE.write_text(json.dumps(corrections, indent=2), encoding="utf-8")


def build_corrections_context(max_items: int = 10) -> str:
    corrections = load_corrections()
    if not corrections:
        return ""
    recent = corrections[-max_items:]
    lines = ["# Past Corrections", "", "Learn from these prior user corrections to improve accuracy:", ""]
    for c in recent:
        parts = []
        if c.get("ai_description") != c.get("corrected_description"):
            parts.append(f'description "{c.get("ai_description")}" was corrected to "{c.get("corrected_description")}"')
        if c.get("ai_client_or_location") != c.get("corrected_client_or_location"):
            parts.append(f'client_or_location "{c.get("ai_client_or_location")}" was corrected to "{c.get("corrected_client_or_location")}"')
        if parts:
            lines.append(f'- **{c.get("source_name", "unknown")}**: {"; ".join(parts)}')
    if len(lines) <= 4:
        return ""
    return "\n".join(lines)
