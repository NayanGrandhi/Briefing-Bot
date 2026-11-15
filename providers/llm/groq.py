"""Groq free tier (OpenAI-compatible chat completions endpoint)."""

from __future__ import annotations

from core import http
from providers.llm.base import LLMError, LLMProvider, LLMRateLimited

DEFAULT_MODEL = "llama-3.3-70b-versatile"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    def complete(self, prompt: str, *, max_tokens: int = 512, json_mode: bool = False) -> str:
        body: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            data = http.post_json(
                ENDPOINT,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
                retries=1,
            )
        except http.HTTPError as exc:
            if exc.status == 429:
                raise LLMRateLimited("Groq rate limit / quota reached") from exc
            raise LLMError(f"Groq request failed: {exc}") from exc
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMError("Groq returned no usable choice") from exc
        if not text:
            raise LLMError("Groq returned empty text")
        return text
