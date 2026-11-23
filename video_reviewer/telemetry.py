"""Optional OpenTelemetry traces: one trace per review batch, a span per clip and per model call.

Off unless an OTLP endpoint is given, through the standard
``OTEL_EXPORTER_OTLP_ENDPOINT`` / ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT``
variables or ``otel_endpoint`` in ``~/.video-renamer/config.json``, and unless
the ``otel`` extra is installed. Spans carry clip file names, so only a
loopback endpoint is accepted unless ``SCOUT_OTEL_ALLOW_REMOTE=1`` is set.

Spans hold names, counts, timings and error categories. They never hold the
prompt, the reply, image bytes, API keys or the provider's raw error text.

Telemetry is best effort. Nothing in here may raise into a review: a missing
package, a dead collector or a broken exporter leaves the review unchanged.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

from video_reviewer.config import read_config

SERVICE_NAME = "scout"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_TOKEN_ATTRIBUTES = {
    "prompt_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "thoughts_tokens": "gen_ai.usage.reasoning.output_tokens",
}

_provider: Any = None
_tracer: Any = None


def enabled() -> bool:
    return _tracer is not None


def _endpoint() -> tuple[str, bool]:
    """The configured endpoint and whether it came from the environment."""
    for name in ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        value = os.environ.get(name, "").strip()
        if value:
            return value, True
    return str(read_config().get("otel_endpoint", "") or "").strip(), False


def _is_local(endpoint: str) -> bool:
    try:
        return (urlsplit(endpoint).hostname or "") in _LOOPBACK
    except ValueError:
        return False


def setup_telemetry(processor: Any = None) -> bool:
    """Turn tracing on. Returns False, without raising, when it stays off.

    ``processor`` is for tests; without it spans go to the configured OTLP endpoint.
    """
    global _provider, _tracer
    try:
        if _tracer is not None:
            return True
        endpoint, from_env = "", False
        if processor is None:
            endpoint, from_env = _endpoint()
            if not endpoint:
                return False
            if not _is_local(endpoint) and os.environ.get("SCOUT_OTEL_ALLOW_REMOTE", "") != "1":
                return False
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        from video_reviewer import __version__

        if processor is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            # From the environment the exporter applies the standard rules itself;
            # a config-file value is a base address and needs the traces path.
            exporter = OTLPSpanExporter() if from_env else OTLPSpanExporter(
                endpoint=endpoint if endpoint.rstrip("/").endswith("/v1/traces") else f"{endpoint.rstrip('/')}/v1/traces"
            )
            processor = BatchSpanProcessor(exporter)
        provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME, "service.version": __version__}))
        provider.add_span_processor(processor)
        _provider, _tracer = provider, provider.get_tracer("video_reviewer")
        return True
    except Exception:  # noqa: BLE001 - telemetry is never a reason to fail to start
        _provider = _tracer = None
        return False


def shutdown_telemetry() -> None:
    """Send what is buffered and turn tracing off."""
    global _provider, _tracer
    provider, _provider, _tracer = _provider, None, None
    try:
        if provider is not None:
            provider.shutdown()
    except Exception:  # noqa: BLE001
        pass


def token_attributes(usage: Any) -> dict[str, int]:
    """Token counts under the OpenTelemetry GenAI attribute names."""
    if not isinstance(usage, dict):
        return {}
    return {
        attribute: usage[name]
        for name, attribute in _TOKEN_ATTRIBUTES.items()
        if isinstance(usage.get(name), int) and not isinstance(usage.get(name), bool)
    }


class span:
    """``with span("name", key=value) as current:`` — a no-op when tracing is off.

    Exceptions from the wrapped code pass through untouched; exceptions from
    telemetry itself are swallowed.
    """

    def __init__(self, name: str, **attributes: Any) -> None:
        self._name = name
        self._attributes = attributes
        self._span: Any = None
        self._token: Any = None

    def __enter__(self) -> "span":
        if _tracer is None:
            return self
        try:
            from opentelemetry import context, trace

            self._span = _tracer.start_span(self._name)
            self._token = context.attach(trace.set_span_in_context(self._span))
            self.set(**self._attributes)
        except Exception:  # noqa: BLE001
            self._span = None
        return self

    def set(self, **attributes: Any) -> None:
        if self._span is None:
            return
        try:
            for name, value in attributes.items():
                if value is not None and value != "":
                    self._span.set_attribute(name, value)
        except Exception:  # noqa: BLE001
            pass

    def fail(self, error_type: str, http_status: int | None = None) -> None:
        """Mark the span failed with a category, never with raw error text."""
        if self._span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            self.set(**{"error.type": error_type, "http.response.status_code": http_status})
            self._span.set_status(Status(StatusCode.ERROR, error_type))
        except Exception:  # noqa: BLE001
            pass

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if self._token is not None:
                from opentelemetry import context

                context.detach(self._token)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._span is not None:
                self._span.end()
        except Exception:  # noqa: BLE001
            pass
        return False
