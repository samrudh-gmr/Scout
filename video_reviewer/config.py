from __future__ import annotations

import json
import os
from pathlib import Path


_CONFIG_DIR = Path.home() / ".video-renamer"
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_CORRECTIONS_FILE = _CONFIG_DIR / "corrections.json"
_MAX_CORRECTIONS = 50
_DEFAULT_CODEX_PROXY_BASE_URL = "http://127.0.0.1:18080/v1"


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


def codex_proxy_base_url() -> str:
    return read_config().get("codex_proxy_base_url", _DEFAULT_CODEX_PROXY_BASE_URL).rstrip("/")


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


# ── API keys ────────────────────────────────────────────────────────────────
# Keys live in their own file, never in config.json, the manifest, or the
# browser. The file is written 0600 inside a 0700 directory so other accounts
# on a shared machine cannot read it, and it is only ever returned to the UI
# masked.
_KEYS_FILE = _CONFIG_DIR / "keys.json"


def _read_keys() -> dict[str, str]:
    if not _KEYS_FILE.exists():
        return {}
    try:
        data = json.loads(_KEYS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_keys(keys: dict[str, str]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    # Create with 0600 from the start so the key is never briefly world-readable.
    fd = os.open(_KEYS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # The mode above only applies when open() creates a new file — POSIX ignores
    # it for a file that already existed (e.g. left world-readable by an older
    # version). Force it every write so permissions can never stay loose.
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(keys, handle, indent=2)


def save_api_key(provider_id: str, api_key: str) -> None:
    keys = _read_keys()
    keys[provider_id] = api_key.strip()
    _write_keys(keys)


def load_api_key(provider_id: str) -> str:
    return _read_keys().get(provider_id, "")


def delete_api_key(provider_id: str) -> bool:
    keys = _read_keys()
    if provider_id not in keys:
        return False
    del keys[provider_id]
    _write_keys(keys)
    return True


def api_key_hint(provider_id: str) -> str:
    """A masked fingerprint safe to show in the UI, e.g. '••••4f2a'."""
    key = load_api_key(provider_id)
    return f"••••{key[-4:]}" if len(key) >= 4 else ("••••" if key else "")
