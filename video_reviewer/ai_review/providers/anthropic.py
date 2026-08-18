from __future__ import annotations

import base64
import json

from video_reviewer.ai_review.models import ReviewRequest
from video_reviewer.ai_review.providers.common import VisionProviderBase, prompt_for


class AnthropicProvider(VisionProviderBase):
    provider_id = "anthropic"
    display_name = "Anthropic Vision"
    default_model = "claude-3-5-haiku-latest"
    cheap_model = "claude-3-5-haiku-latest"
    accurate_model = "claude-3-7-sonnet-latest"
    env_key_names = ("ANTHROPIC_API_KEY",)
    missing_sdk_message = "The anthropic SDK is not installed. Run `uv sync`."
    key_message = "Anthropic SDK ready. Paste an API key or set ANTHROPIC_API_KEY. Create one at https://console.anthropic.com/settings/keys"
    ready_message = "Anthropic Vision API ready."

    def _sdk_installed(self) -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _generate(self, api_key: str, model: str, request: ReviewRequest) -> dict:
        from anthropic import Anthropic

        content = [{"type": "text", "text": prompt_for(request)}]
        for frame in request.frames[: request.policy.max_frames]:
            content.append({"type": "image", "source": {"type": "base64", "media_type": frame.mime_type, "data": base64.b64encode(frame.data).decode("ascii")}})
        response = Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=700, temperature=0.15,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            from video_reviewer.ai_review.models import AiReviewError, ErrorCategory
            raise AiReviewError(f"Model did not return valid JSON: {text[:160]}", ErrorCategory.MALFORMED_RESPONSE) from exc
        data["_raw_text"] = text
        return data
