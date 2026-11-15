"""AIService: the only place prompts live. Cached, budgeted and always optional.

Every method returns None (or an empty result) when the LLM is missing, rate-limited,
over budget or returns garbage - callers then use their non-AI fallback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import date

from core.store import Store
from providers.llm.base import LLMProvider, LLMRateLimited

log = logging.getLogger("ai")


def parse_json_array(text: str) -> list:
    """Extract a JSON array from model output, tolerating code fences and chatter."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array found")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("not a list")
    return data


def _clean(text: str) -> str:
    return " ".join(text.split()).strip("\"' ")


def _is_json_array(text: str) -> bool:
    try:
        parse_json_array(text)
        return True
    except ValueError:
        return False


class AIService:
    def __init__(
        self,
        llm: LLMProvider | None,
        store: Store,
        daily_budget: int = 30,
        cache_ttl_hours: int = 48,
        today: date | None = None,
    ) -> None:
        self.llm = llm
        self.store = store
        self.daily_budget = daily_budget
        self.cache_ttl = cache_ttl_hours * 3600
        self.today = today or date.today()
        self._tripped = False  # set after a rate-limit: stop calling for this run

    @property
    def enabled(self) -> bool:
        return self.llm is not None and not self._tripped

    def _ask(
        self,
        prompt: str,
        max_tokens: int,
        json_mode: bool = False,
        accept: Callable[[str], bool] | None = None,
    ) -> str | None:
        """Cached, budgeted LLM call. Only responses passing `accept` are cached or returned."""
        if not self.enabled:
            return None
        assert self.llm is not None
        key = hashlib.sha256(f"{max_tokens}|{json_mode}|{prompt}".encode()).hexdigest()
        cached = self.store.cache_get(key, self.cache_ttl)
        if cached is not None:
            log.info("llm cache hit")
            return cached
        counter = f"llm_calls:{self.today.isoformat()}"
        if int(self.store.get_state(counter, "0") or 0) >= self.daily_budget:
            log.warning("llm daily budget reached", extra={"budget": self.daily_budget})
            return None
        self.store.incr(counter)
        try:
            text = self.llm.complete(prompt, max_tokens=max_tokens, json_mode=json_mode)
        except LLMRateLimited:
            log.warning("llm rate limited - disabling AI for this run")
            self._tripped = True
            return None
        except Exception as exc:  # noqa: BLE001 - AI is optional: nothing here may escape
            log.warning("llm call failed", extra={"err": f"{type(exc).__name__}: {exc}"})
            return None
        if accept is not None and not accept(text):
            log.warning("llm output rejected")
            return None
        self.store.cache_set(key, text)
        return text

    # ---- tasks -------------------------------------------------------------
    def score_and_summarize(
        self, stories: list[tuple[str, str]], interests: str
    ) -> dict[int, tuple[str, float]]:
        """One batched call for all stories -> {index: (one-line summary, relevance 1-10)}."""
        if not stories:
            return {}
        lines = [f"{i}. {t[:120]} | {s[:180]}" for i, (t, s) in enumerate(stories)]
        prompt = (
            "[task:news]\nYou are a concise news editor. "
            f"Reader interests: {interests or 'general news'}.\n"
            'For every numbered story output a JSON array of objects {"i": <number>, '
            '"s": "<one-line factual summary, max 22 words>", "r": <relevance 1-10 to the '
            "interests>}. Return JSON only.\n\n" + "\n".join(lines)
        )
        raw = self._ask(
            prompt, max_tokens=60 * len(stories) + 100, json_mode=True, accept=_is_json_array
        )
        if raw is None:
            return {}
        try:
            rows = parse_json_array(raw)
        except ValueError as exc:
            log.warning("llm news output unparseable", extra={"err": str(exc)})
            return {}
        out: dict[int, tuple[str, float]] = {}
        for row in rows:
            try:
                i = int(row["i"])
                if 0 <= i < len(stories):
                    score = max(1.0, min(10.0, float(row["r"])))
                    out[i] = (str(row.get("s", "")).strip(), score)
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def day_intro(self, facts: list[str]) -> str | None:
        if not facts:
            return None
        prompt = (
            "[task:intro]\nWrite exactly 2 short, friendly sentences ('your day at a glance') "
            "for a morning briefing using only these facts. No greeting, no emojis, "
            "do not invent anything.\nFacts:\n" + "\n".join(f"- {f}" for f in facts)
        )
        text = self._ask(prompt, max_tokens=120, accept=lambda t: 20 <= len(_clean(t)) <= 400)
        return _clean(text) if text else None

    def tip(self, topic: str, avoid: list[str]) -> str | None:
        recent = "\n".join(f"- {a}" for a in avoid[-10:]) or "(none)"
        prompt = (
            f"[task:tip]\nGive one practical, specific tip about {topic} in at most 35 words. "
            "Plain text, no markdown, no preamble. Do not repeat or paraphrase these recent "
            f"tips:\n{recent}"
        )
        text = self._ask(prompt, max_tokens=100, accept=lambda t: 10 <= len(_clean(t)) <= 300)
        return _clean(text) if text else None
