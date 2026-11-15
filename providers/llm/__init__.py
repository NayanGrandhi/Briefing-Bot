"""LLM provider registry + failover chain. Add a provider = add a class and one dict entry."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping

from core.config import LLMConfig
from providers.llm.base import LLMError, LLMProvider, LLMRateLimited
from providers.llm.gemini import GeminiProvider
from providers.llm.groq import GroqProvider

log = logging.getLogger("llm")

# name -> (env var holding the key, constructor)
PROVIDERS: dict[str, tuple[str, Callable[[str, str | None], LLMProvider]]] = {
    "gemini": ("GEMINI_API_KEY", lambda key, model: GeminiProvider(key, model)),
    "groq": ("GROQ_API_KEY", lambda key, model: GroqProvider(key, model)),
}

__all__ = ["PROVIDERS", "FallbackLLM", "LLMError", "LLMProvider", "LLMRateLimited", "build_llm"]


class FallbackLLM(LLMProvider):
    """Try each provider in order; first success wins."""

    name = "chain"

    def __init__(self, providers: list[LLMProvider]) -> None:
        self.providers = providers

    def complete(self, prompt: str, *, max_tokens: int = 512, json_mode: bool = False) -> str:
        last: LLMError | None = None
        for provider in self.providers:
            try:
                return provider.complete(prompt, max_tokens=max_tokens, json_mode=json_mode)
            except LLMError as exc:
                log.warning(
                    "llm provider failed", extra={"provider": provider.name, "err": str(exc)}
                )
                last = exc
        raise last or LLMError("no LLM providers configured")


def build_llm(cfg: LLMConfig, env: Mapping[str, str] | None = None) -> LLMProvider | None:
    """Build the configured provider chain, or None if no API key is available."""
    env = os.environ if env is None else env
    chain: list[LLMProvider] = []
    for name, model in ((cfg.provider, cfg.model), (cfg.fallback_provider, cfg.fallback_model)):
        if not name:
            continue
        if name not in PROVIDERS:
            log.warning("unknown llm provider", extra={"provider": name})
            continue
        env_var, ctor = PROVIDERS[name]
        key = env.get(env_var)
        if key:
            chain.append(ctor(key, model))
        else:
            log.info("llm provider skipped (no key)", extra={"provider": name, "env": env_var})
    if not chain:
        return None
    return chain[0] if len(chain) == 1 else FallbackLLM(chain)
