import json
from datetime import timedelta

import pytest

from core.ai import parse_json_array
from core.store import Preferences
from providers import rss
from providers.llm.base import LLMError, LLMRateLimited
from sections import news
from sections.news import Story, dedupe, keyword_score, rank
from tests.conftest import NOW, FakeLLM


def S(title, source="A", snippet="", hours=2, url=None):
    return Story(news.story_id(url or f"https://x.test/{title}", title), title,
                 url or f"https://x.test/{title}", source, snippet, NOW - timedelta(hours=hours))  # fmt: skip


RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>OpenAI ships new model</title><link>https://a.test/1</link>
<description>&lt;p&gt;Big &amp;amp; fast.&lt;/p&gt;</description>
<pubDate>Sun, 15 Nov 2026 03:00:00 GMT</pubDate></item>
<item><title>Arsenal win derby</title><link>https://a.test/2</link><description>Late goal.</description>
<pubDate>Sat, 14 Nov 2026 20:00:00 GMT</pubDate></item></channel></rss>"""

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom story</title>
<link href="https://a.test/atom"/><summary>Hi</summary><updated>2026-11-15T01:00:00Z</updated>
</entry></feed>"""


def test_parse_rss_strips_html_and_parses_dates():
    e = rss.parse_feed(RSS, "A")
    assert [x.title for x in e] == ["OpenAI ships new model", "Arsenal win derby"]
    assert e[0].summary == "Big & fast." and e[0].published.tzinfo is not None


def test_parse_atom():
    e = rss.parse_feed(ATOM, "B")
    assert e[0].link == "https://a.test/atom" and e[0].published.year == 2026


def test_xml_bombs_are_rejected():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><rss>&b;</rss>'
    with pytest.raises(Exception):  # noqa: B017 - defusedxml raises EntitiesForbidden
        rss.parse_feed(bomb, "A")


def test_dedupe_merges_similar_titles_and_records_sources():
    out = dedupe([
        S("OpenAI releases new open-weight reasoning model", "A"),
        S("OpenAI launches open-weight reasoning model for developers", "B"),
        S("Arsenal win the derby", "C"),
    ])  # fmt: skip
    assert len(out) == 2 and out[0].also == ["B"]


def test_keyword_scoring_and_feed_topic(config):
    a, b, c = S("Nvidia GPU supply grows"), S("Local bake sale"), S("Weekend results")
    for s, ft in ((a, None), (b, None), (c, "Football")):
        keyword_score(s, config.topics, ft)
    assert a.kw_score > 3 and a.topics == ["Tech & AI"]
    assert b.kw_score == 1.0 and not b.topics
    assert "Football" in c.topics


def test_feedback_reranks_toward_preferred_source_and_topic():
    a = S("Story one", "Liked", hours=5)
    b = S("Story two", "Disliked", hours=5)
    a.llm_score = b.llm_score = 6
    a.topics, b.topics = ["Football"], ["Tech & AI"]
    prefs = Preferences(source={"Liked": 0.9, "Disliked": -0.9}, topic={"Football": 0.8})
    assert rank([b, a], prefs, NOW, 2)[0] is a
    assert rank([b, a], Preferences(), NOW, 2)[0] in (a, b)


def test_rank_enforces_topic_diversity():
    items = []
    for i in range(6):
        s = S(f"Tech {i}", "A")
        s.topics, s.llm_score = ["Tech & AI"], 9
        items.append(s)
    f = S("Football one", "B")
    f.topics, f.llm_score = ["Football"], 6
    picked = rank(items + [f], Preferences(), NOW, 5, max_topic_share=0.6)
    assert f in picked and len(picked) == 5


def test_parse_json_array_tolerates_fences_and_chatter():
    assert parse_json_array('Sure!\n```json\n[{"i":0}]\n```') == [{"i": 0}]
    with pytest.raises(ValueError):
        parse_json_array("no json here")


def _patch_feeds(monkeypatch, payloads):
    def fake(feed):
        p = payloads[feed.name]
        if isinstance(p, Exception):
            raise p
        return rss.parse_feed(p, feed.name)

    monkeypatch.setattr(news.rss, "fetch_feed", fake)


def test_section_uses_llm_scores_and_summaries(monkeypatch, make_ctx):
    _patch_feeds(monkeypatch, {"Feed A": RSS, "Feed B": ATOM})
    llm = FakeLLM(json.dumps([
        {"i": 0, "s": "OpenAI released a model.", "r": 9},
        {"i": 1, "s": "Arsenal won a derby.", "r": 8},
        {"i": 2, "s": "Irrelevant.", "r": 1},
    ]))  # fmt: skip
    ctx = make_ctx(llm)
    sec = news.NewsSection(ctx, {"max_items": 5}).fetch()
    assert len(llm.calls) == 1  # ONE batched call for all stories
    assert len(sec.items) == 2  # the score-1 story is dropped
    assert "OpenAI released a model." in sec.items[0].text and sec.items[0].feedback


def test_section_falls_back_without_llm(monkeypatch, make_ctx):
    _patch_feeds(monkeypatch, {"Feed A": RSS, "Feed B": ATOM})
    sec = news.NewsSection(make_ctx(None), {}).fetch()
    assert sec.ok  # keyword ranking only
    texts = " ".join(i.text for i in sec.items)
    assert "OpenAI" in texts and "Arsenal" in texts
    assert "Atom story" not in texts  # matches no topic -> below min_score


@pytest.mark.parametrize("err", [LLMError("bad"), LLMRateLimited("429")])
def test_section_survives_llm_errors(monkeypatch, make_ctx, err):
    _patch_feeds(monkeypatch, {"Feed A": RSS, "Feed B": ATOM})
    ctx = make_ctx(FakeLLM(err))
    sec = news.NewsSection(ctx, {}).fetch()
    assert sec.items
    if isinstance(err, LLMRateLimited):
        assert not ctx.ai.enabled  # breaker tripped for the rest of the run


def test_one_dead_feed_is_tolerated_but_all_dead_raises(monkeypatch, make_ctx):
    _patch_feeds(monkeypatch, {"Feed A": RuntimeError("x"), "Feed B": RSS})
    assert news.NewsSection(make_ctx(None), {}).fetch().items
    _patch_feeds(monkeypatch, {"Feed A": RuntimeError("x"), "Feed B": RuntimeError("y")})
    with pytest.raises(RuntimeError):
        news.NewsSection(make_ctx(None), {}).fetch()


def test_old_and_already_sent_stories_are_skipped(monkeypatch, make_ctx, store):
    _patch_feeds(monkeypatch, {"Feed A": RSS, "Feed B": ATOM})
    ctx = make_ctx(None)
    sid = news.story_id("https://a.test/1", "OpenAI ships new model")
    store.remember_item(sid, {"url": "https://a.test/1", "title": "t", "source": "A"})
    sec = news.NewsSection(ctx, {"max_age_hours": 3}).fetch()
    texts = " ".join(i.text for i in sec.items)
    assert "OpenAI ships" not in texts  # sent already
    assert "Arsenal" not in texts  # older than 3h
