from __future__ import annotations

import json
import logging

from openai import OpenAI

from postowl.config import LLMConfig

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = OpenAI(base_url=config.base_url, api_key=config.api_key)

    @property
    def openai_client(self) -> OpenAI:
        return self._client

    def chat(self, messages: list[dict], *, temperature: float | None = None,
             max_tokens: int | None = None, json_mode: bool = False) -> str:
        kwargs: dict = {
            "model": self.config.chat_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        return content.strip()

    def chat_json(self, messages: list[dict], **kwargs) -> dict:
        text = self.chat(messages, json_mode=True, **kwargs)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON response: %s", text[:200])
            return {}
