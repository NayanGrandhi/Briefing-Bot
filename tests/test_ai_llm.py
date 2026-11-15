import json

import pytest

from core.ai import AIService
from core.config import LLMConfig
from core.http import HTTPError
from providers import llm as llm_pkg
from providers.llm import FallbackLLM, build_llm
from providers.llm import gemini as gm
from providers.llm import groq as gq
from providers.llm.base import LLMError, LLMRateLimited
from tests.conftest import NOW, FakeLLM


def test_gemini_parses_and_sends_key_in_header(monkeypatch):
    seen = {}

    def fake(url, **kw):
        seen.update(url=url, **kw)
        return {"candidates": [{"content": {"parts": [{"text": "hello "}, {"text": "world"}]}}]}

    monkeypatch.setattr(gm.http, "post_json", fake)
    out = gm.GeminiProvider("KEY123", "gemini-2.5-flash").complete("hi", json_mode=True)
    assert out == "hello world"
    assert "KEY123" not in seen["url"] and seen["headers"]["x-goog-api-key"] == "KEY123"
    assert seen["json"]["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize("status,exc", [(429, LLMRateLimited), (500, LLMError)])
def test_gemini_and_groq_map_http_errors(monkeypatch, status, exc):
    def boom(*a, **k):
        raise HTTPError("x", status)

    for mod, cls in ((gm, gm.GeminiProvider), (gq, gq.GroqProvider)):
        monkeypatch.setattr(mod.http, "post_json", boom)
        with pytest.raises(exc):
            cls("k").complete("p")


def test_gemini_and_groq_reject_malformed_payloads(monkeypatch):
    monkeypatch.setattr(gm.http, "post_json", lambda *a, **k: {"candidates": []})
    with pytest.raises(LLMError):
        gm.GeminiProvider("k").complete("p")
    monkeypatch.setattr(gq.http, "post_json", lambda *a, **k: {"choices": [{"message": {}}]})
    with pytest.raises(LLMError):
        gq.GroqProvider("k").complete("p")


def test_groq_parses(monkeypatch):
    monkeypatch.setattr(
        gq.http, "post_json", lambda *a, **k: {"choices": [{"message": {"content": " ok "}}]}
    )
    assert gq.GroqProvider("k").complete("p") == "ok"


def test_fallback_chain_and_builder():
    chain = FallbackLLM([FakeLLM(LLMError("a down")), FakeLLM("from b")])
    assert chain.complete("x") == "from b"
    with pytest.raises(LLMError):
        FallbackLLM([FakeLLM(LLMError("a")), FakeLLM(LLMError("b"))]).complete("x")

    cfg = LLMConfig(provider="gemini", fallback_provider="groq")
    assert build_llm(cfg, {}) is None  # no keys -> AI simply off
    assert build_llm(cfg, {"GEMINI_API_KEY": "k"}).name == "gemini"
    assert isinstance(build_llm(cfg, {"GEMINI_API_KEY": "k", "GROQ_API_KEY": "g"}), FallbackLLM)
    assert build_llm(LLMConfig(provider="nonsense"), {"X": "y"}) is None
    assert set(llm_pkg.PROVIDERS) >= {"gemini", "groq"}


def test_ai_caches_identical_prompts(store):
    llm = FakeLLM("Use short, simple tips every day for better focus.")
    ai = AIService(llm, store, today=NOW.date())
    a = ai.tip("python", [])
    b = ai.tip("python", [])
    assert a == b and len(llm.calls) == 1


def test_ai_respects_daily_budget(store):
    llm = FakeLLM(*["A decent length tip for the day here."] * 5)
    ai = AIService(llm, store, daily_budget=2, today=NOW.date())
    for i in range(4):
        ai.tip(f"topic {i}", [])
    assert len(llm.calls) == 2


def test_ai_trips_breaker_on_rate_limit(store):
    llm = FakeLLM(LLMRateLimited("429"), "never reached, tip tip tip")
    ai = AIService(llm, store, today=NOW.date())
    assert ai.tip("a", []) is None and not ai.enabled
    assert ai.tip("b", []) is None and len(llm.calls) == 1


def test_score_and_summarize_validates_rows(store):
    rows = [{"i": 0, "s": "Good", "r": 15}, {"i": 9, "s": "oob", "r": 5},
            {"i": 1, "s": "x", "r": "high"}, {"bad": 1}]  # fmt: skip
    ai = AIService(FakeLLM(json.dumps(rows)), store, today=NOW.date())
    out = ai.score_and_summarize([("a", ""), ("b", "")], "tech")
    assert out == {0: ("Good", 10.0)}  # clamped to 10; invalid rows ignored


def test_score_and_summarize_handles_garbage(store):
    ai = AIService(FakeLLM("sorry, I can't"), store, today=NOW.date())
    assert ai.score_and_summarize([("a", "")], "x") == {}


def test_intro_rejects_bad_length(store):
    assert AIService(FakeLLM("too short"), store, today=NOW.date()).day_intro(["f"]) is None
    ok = "It is warm today. You have two meetings before lunch."
    assert AIService(FakeLLM(f'"{ok}"'), store, today=NOW.date()).day_intro(["f"]) == ok


def test_ai_disabled_without_provider(store):
    ai = AIService(None, store, today=NOW.date())
    assert (
        not ai.enabled
        and ai.day_intro(["f"]) is None
        and ai.score_and_summarize([("a", "")], "") == {}
    )


def test_rejected_output_is_not_cached(store):
    llm = FakeLLM("too short", "A perfectly fine tip about git worktrees here.")
    ai = AIService(llm, store, today=NOW.date())
    assert ai.tip("git", []) is None  # rejected, not cached...
    assert ai.tip("git", []) == "A perfectly fine tip about git worktrees here."  # ...so retried


def test_unexpected_provider_exceptions_never_escape(store):
    ai = AIService(FakeLLM(ValueError("weird")), store, today=NOW.date())
    assert ai.day_intro(["fact"]) is None


def test_non_json_200_becomes_http_error(monkeypatch):
    import requests

    from core import http

    r = requests.Response()
    r.status_code, r._content = 200, b"<html>captive portal</html>"
    monkeypatch.setattr(requests, "request", lambda *a, **k: r)
    with pytest.raises(http.HTTPError):
        http.get_json("https://x.test/a")
