"""Gemini API (free tier) via plain REST - no SDK dependency."""

from __future__ import annotations

from core import http
from providers.llm.base import LLMError, LLMProvider, LLMRateLimited

DEFAULT_MODEL = "gemini-2.5-flash"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    def complete(self, prompt: str, *, max_tokens: int = 512, json_mode: bool = False) -> str:
        gen: dict[str, object] = {"maxOutputTokens": max_tokens, "temperature": 0.3}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        if "2.5" in self.model:
            gen["thinkingConfig"] = {"thinkingBudget": 0}  # don't burn free-tier tokens thinking
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen}
        try:
            data = http.post_json(
                ENDPOINT.format(model=self.model),
                json=body,
                headers={"x-goog-api-key": self.api_key},
                timeout=self.timeout,
                retries=1,
            )
        except http.HTTPError as exc:
            if exc.status == 429:
                raise LLMRateLimited("Gemini rate limit / quota reached") from exc
            raise LLMError(f"Gemini request failed: {exc}") from exc
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Gemini returned no usable candidate") from exc
        if not text:
            raise LLMError("Gemini returned empty text")
        return text
