from __future__ import annotations

import base64
import json

from video_reviewer.ai_review.models import ReviewRequest
from video_reviewer.ai_review.providers.common import VisionProviderBase, prompt_for


class OpenAIProvider(VisionProviderBase):
    provider_id = "openai"
    display_name = "OpenAI Vision"
    default_model = "gpt-4o-mini"
    cheap_model = "gpt-4o-mini"
    accurate_model = "gpt-4o"
    env_key_names = ("OPENAI_API_KEY",)
    missing_sdk_message = "The openai SDK is not installed. Run `uv sync`."
    key_message = "OpenAI SDK ready. Paste an API key or set OPENAI_API_KEY. Create one at https://platform.openai.com/api-keys"
    ready_message = "OpenAI Vision API ready."

    def _sdk_installed(self) -> bool:
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def _generate(self, api_key: str, model: str, prompt: str, frames) -> dict:
        from openai import OpenAI

        content = [{"type": "text", "text": prompt}]
        for frame in frames:
            encoded = base64.b64encode(frame.data).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{frame.mime_type};base64,{encoded}"}})
        response = OpenAI(api_key=api_key).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=0.15,
        )
        text = (response.choices[0].message.content or "").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            from video_reviewer.ai_review.models import AiReviewError, ErrorCategory
            raise AiReviewError(f"Model did not return valid JSON: {text[:160]}", ErrorCategory.MALFORMED_RESPONSE) from exc
        data["_raw_text"] = text
        return data
