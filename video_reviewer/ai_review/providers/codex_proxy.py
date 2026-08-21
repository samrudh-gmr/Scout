"""Loopback-only OpenAI-compatible bridge for a user's Codex subscription.

Video Renamer never reads, copies, or writes Codex OAuth credentials.  The
UV-managed proxy process owns the official Codex CLI auth file; this module
only starts that local process and talks to its OpenAI-compatible HTTP API.
"""
from __future__ import annotations

import base64
import json
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from openai import OpenAI

from video_reviewer.ai_review.models import AiReviewError, ErrorCategory, ProviderConfig, ProviderStatus
from video_reviewer.ai_review.providers.common import VisionProviderBase
from video_reviewer.config import codex_proxy_base_url, load_api_key, save_api_key

_PROXY_KEY_PROVIDER = "codex-proxy"
_PROXY_START_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProxyStartResult:
    available: bool
    message: str


def _validated_base_url(base_url: str) -> tuple[str, int]:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The local Codex provider may only use a localhost HTTP endpoint.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The local Codex provider URL must be a plain localhost endpoint.")
    port = parsed.port or 80
    if not 1 <= port <= 65535:
        raise ValueError("The local Codex provider port is invalid.")
    path = parsed.path.rstrip("/")
    if path != "/v1":
        raise ValueError("The local Codex provider URL must end in /v1.")
    return f"http://{parsed.hostname}:{port}/v1", port


def proxy_is_healthy(base_url: str, api_key: str) -> bool:
    normalized, _ = _validated_base_url(base_url)
    origin = normalized.removesuffix("/v1")
    request = urllib.request.Request(f"{origin}/healthz", headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


def _proxy_executable() -> str | None:
    name = "openai-api-server-via-codex"
    executable = Path(sys.executable)
    candidates = [
        executable.with_name(name),
        Path(getattr(sys, "_MEIPASS", "")) / name,
        executable.parent / "_internal" / name,
        executable.parent.parent / "Frameworks" / name,
    ]
    for bundled in candidates:
        if bundled.is_file() and bundled.exists():
            return str(bundled)
    return shutil.which(name)


def _stored_or_generated_key() -> str:
    key = load_api_key(_PROXY_KEY_PROVIDER).strip()
    if key:
        return key
    key = secrets.token_urlsafe(32)
    save_api_key(_PROXY_KEY_PROVIDER, key)
    return key


def ensure_proxy_running(base_url: str, api_key: str) -> ProxyStartResult:
    """Start the bundled proxy if necessary, never exposing its credentials."""
    try:
        normalized, port = _validated_base_url(base_url)
    except ValueError as exc:
        return ProxyStartResult(False, str(exc))

    with _PROXY_START_LOCK:
        if proxy_is_healthy(normalized, api_key):
            return ProxyStartResult(True, "Local Codex subscription provider is ready.")

        executable = _proxy_executable()
        if not executable:
            return ProxyStartResult(False, "The local Codex proxy is missing. Run `uv sync` from Video Renamer and try again.")
        command = [
            executable,
            "--host", "127.0.0.1",
            "--port", str(port),
            "--api-key", api_key,
            "--default-model", "gpt-5.6-luna",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return ProxyStartResult(False, "Video Renamer could not start its local Codex proxy. Run `uv sync` and try again.")

        for _ in range(10):
            time.sleep(0.15)
            if proxy_is_healthy(normalized, api_key):
                return ProxyStartResult(True, "Local Codex subscription provider is ready.")
            if process.poll() is not None:
                return ProxyStartResult(
                    False,
                    "Codex needs its one-time local sign-in. Run `codex login`, then select this provider again.",
                )
    return ProxyStartResult(False, "The local Codex proxy did not become ready. Check your Codex sign-in and try again.")


class CodexProxyProvider(VisionProviderBase):
    provider_id = _PROXY_KEY_PROVIDER
    display_name = "OpenAI Codex Subscription (local)"
    default_model = "gpt-5.6-luna"
    cheap_model = "gpt-5.6-luna"
    accurate_model = "gpt-5.6-sol"
    env_key_names = ()
    missing_sdk_message = "The OpenAI SDK is not installed. Run uv sync."
    key_message = "Local Codex subscription provider is unavailable."
    ready_message = "Local Codex subscription provider is ready."

    def _sdk_installed(self) -> bool:
        return True

    def resolve_api_key(self, api_key: str | None = None) -> str | None:
        return (api_key or "").strip() or _stored_or_generated_key()

    def status(self, config: ProviderConfig | None = None) -> ProviderStatus:
        key = self.resolve_api_key(config.api_key if config else None) or _stored_or_generated_key()
        started = ensure_proxy_running(codex_proxy_base_url(), key)
        if not started.available:
            return ProviderStatus(
                provider_id=self.provider_id, display_name=self.display_name, available=False,
                has_key=False, message=started.message, default_model=self.default_model,
                cheap_model=self.cheap_model, accurate_model=self.accurate_model,
            )
        try:
            models = tuple(self.list_models(key))
        except AiReviewError as exc:
            return ProviderStatus(
                provider_id=self.provider_id, display_name=self.display_name, available=False,
                has_key=True, message=str(exc), default_model=self.default_model,
                cheap_model=self.cheap_model, accurate_model=self.accurate_model,
            )
        return ProviderStatus(
            provider_id=self.provider_id, display_name=self.display_name, available=True,
            has_key=True, message=self.ready_message, default_model=self.default_model,
            cheap_model=self.cheap_model, accurate_model=self.accurate_model, models=models,
        )

    def list_models(self, api_key: str | None = None) -> list[str]:
        key = self.resolve_api_key(api_key)
        try:
            base_url, _ = _validated_base_url(codex_proxy_base_url())
            request = urllib.request.Request(f"{base_url}/models", headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (ValueError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            raise AiReviewError("The local Codex provider is not ready. Select it again after Codex has signed in.", ErrorCategory.PROVIDER_UNAVAILABLE) from exc
        values = payload.get("data", []) if isinstance(payload, dict) else []
        models = [str(item.get("id", "")).strip() for item in values if isinstance(item, dict)]
        return sorted({model for model in models if model})

    def _generate(self, api_key: str, model: str, prompt: str, frames) -> dict:
        base_url, _ = _validated_base_url(codex_proxy_base_url())
        # status() is what normally boots the local proxy, but not every caller
        # checks status() first (chat, in particular, calls generate_json()
        # directly) — so make sure it is actually up right before we use it,
        # not just when someone happened to poll status first.
        started = ensure_proxy_running(codex_proxy_base_url(), api_key)
        if not started.available:
            raise AiReviewError(started.message, ErrorCategory.PROVIDER_UNAVAILABLE)
        content = [{"type": "text", "text": prompt}]
        for frame in frames:
            encoded = base64.b64encode(frame.data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{frame.mime_type};base64,{encoded}"}})
        response = OpenAI(api_key=api_key, base_url=base_url).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
        text = (response.choices[0].message.content or "").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AiReviewError("Codex did not return valid naming JSON. Try again or use another model.", ErrorCategory.MALFORMED_RESPONSE) from exc
        data["_raw_text"] = text
        return data
