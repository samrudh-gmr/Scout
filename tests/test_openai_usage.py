from __future__ import annotations

import json

PAYLOAD = {
    "scene": "robot_process", "part": "Aluminum Wheel", "industry": "Specialty Vehicle", "description": "Sanding",
    "client_or_location": "GMR HQ", "confidence": 0.95, "rationale": "robot sands a wheel", "flags": [],
}


def test_the_chatgpt_bridge_reports_token_counts_when_the_reply_has_them(monkeypatch) -> None:
    from types import SimpleNamespace

    from video_reviewer.ai_review.providers import codex_proxy

    reply = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(PAYLOAD)))],
        usage=SimpleNamespace(prompt_tokens=900, completion_tokens=70, total_tokens=970),
    )

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: reply))

    monkeypatch.setattr(codex_proxy, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(codex_proxy, "ensure_proxy_running", lambda url, key: codex_proxy.ProxyStartResult(True, "ok"))
    data = codex_proxy.CodexProxyProvider()._generate("k", "gpt-test", "p", [])
    assert data["_usage"] == {"prompt_tokens": 900, "output_tokens": 70, "total_tokens": 970}

    reply.usage = None
    assert codex_proxy.CodexProxyProvider()._generate("k", "gpt-test", "p", [])["_usage"] is None
