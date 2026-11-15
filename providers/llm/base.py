"""The swappable LLM interface. Everything else in the bot depends only on this."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(Exception):
    """Any failure to get a usable completion."""


class LLMRateLimited(LLMError):
    """Quota/rate limit hit - callers should stop calling for the rest of the run."""


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete(self, prompt: str, *, max_tokens: int = 512, json_mode: bool = False) -> str:
        """Return the model's text for `prompt` or raise LLMError."""
