import pytest

from providers import markets as mk
from sections import markets as ms
from sections import snippet


def chart(closes, last=None, stamps=None):
    return {"chart": {"result": [{
        "meta": {"regularMarketPrice": last, "exchangeTimezoneName": "America/New_York"},
        "timestamp": stamps or [1_763_000_000 + i * 86400 for i in range(len(closes))],
        "indicators": {"quote": [{"close": closes}]}}]}}  # fmt: skip


def test_daily_change_uses_previous_session_not_range_start(monkeypatch):
    monkeypatch.setattr(mk.http, "get_json", lambda *a, **k: chart([50, 100, None, 110], last=110))
    q = mk.yahoo_daily_change("NVDA", "Nvidia")
    assert q.change_pct == pytest.approx(10.0)  # 110 vs 100, ignoring None and the 50


def test_daily_change_needs_two_closes(monkeypatch):
    monkeypatch.setattr(mk.http, "get_json", lambda *a, **k: chart([100], last=100))
    with pytest.raises(ValueError):
        mk.yahoo_daily_change("X", "X")


def test_coingecko_and_movers_filtering(monkeypatch):
    monkeypatch.setattr(mk.http, "get_json", lambda *a, **k: {"bitcoin": {"usd_24h_change": 2.5}})
    out = mk.coingecko_changes([("bitcoin", "Bitcoin"), ("ethereum", "Ethereum")])
    assert [(q.name, q.change_pct) for q in out] == [("Bitcoin", 2.5)]

    rows = [
        {"symbol": "BIG", "shortName": "Big Co", "regularMarketChangePercent": 12,
         "marketCap": 5e10, "regularMarketPrice": 100},
        {"symbol": "PENNY", "regularMarketChangePercent": 90, "marketCap": 1e6,
         "regularMarketPrice": 0.2},
    ]  # fmt: skip
    monkeypatch.setattr(
        mk.http, "get_json", lambda *a, **k: {"finance": {"result": [{"quotes": rows}]}}
    )
    movers = mk.yahoo_movers("day_gainers", 1e10, 5)
    assert [m.name for m in movers] == ["Big Co (BIG)"]


def test_format_flags_and_stale_note():
    from datetime import date

    q = mk.Quote("Nvidia", -6.2, date(2026, 11, 13))
    out = ms.fmt(q, date(2026, 11, 15), 5)
    assert "🔴" in out and "−6.2%" in out and "🔥" in out and "Fri close" in out


def test_section_survives_partial_failures(monkeypatch, make_ctx):
    def yahoo(symbol, name):
        if symbol == "BAD":
            raise RuntimeError("nope")
        return mk.Quote(name, 1.0)

    def crypto(coins):
        raise RuntimeError("coingecko down")

    monkeypatch.setattr(ms.markets, "yahoo_daily_change", yahoo)
    monkeypatch.setattr(ms.markets, "coingecko_changes", crypto)
    opts = {"symbols": [{"symbol": "OK", "name": "Fine"}, {"symbol": "BAD"}],
            "crypto": [{"id": "bitcoin"}]}  # fmt: skip
    s = ms.MarketsSection(make_ctx(), opts).fetch()
    assert "Fine +1.0%" in s.lines[0] and len(s.lines) == 1


def test_section_raises_when_every_source_fails(monkeypatch, make_ctx):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(ms.markets, "yahoo_daily_change", boom)
    with pytest.raises(RuntimeError):
        ms.MarketsSection(make_ctx(), {"symbols": [{"symbol": "A"}]}).fetch()


def test_snippet_uses_llm_then_curated_fallback(make_ctx, store):
    from tests.conftest import FakeLLM

    tip = "Use git switch - to jump to the previous branch."
    s = snippet.SnippetSection(make_ctx(FakeLLM(tip)), {}).fetch()
    assert "git switch" in s.lines[0] and store.get_json("recent_tips", []) == [tip]
    s2 = snippet.SnippetSection(make_ctx(None), {}).fetch()
    assert s2.lines[0]  # curated list kicks in with no LLM


def test_curated_tips_are_all_valid_and_rotate():
    assert snippet.curated_tip(0) != snippet.curated_tip(1)
    assert all(len(snippet.curated_tip(i)) < 300 for i in range(40))
