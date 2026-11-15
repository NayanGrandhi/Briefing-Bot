"""News: RSS -> dedupe -> keyword prefilter -> (LLM score + 1-line summary) -> feedback re-rank."""

from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher

from core.config import Feed, Topic
from core.models import Item, Section
from core.store import Preferences
from core.topics import effective_topics
from providers import rss
from sections.base import SectionPlugin, register

log = logging.getLogger("news")

_STOP = frozenset(
    """the a an and or of to in on for with at by from is are was be as it its this that will
after over new says say report reports""".split()  # noqa: SIM905
)


@dataclass
class Story:
    id: str
    title: str
    url: str
    source: str
    snippet: str
    published: datetime | None
    also: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    kw_score: float = 1.0
    llm_score: float | None = None
    summary: str = ""
    final: float = 0.0
    feed: str = ""


def story_id(url: str, title: str) -> str:
    key = (url.split("?")[0].rstrip("/") or title).lower()
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2 and w not in _STOP}


def similar(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and len(ta & tb) / len(ta | tb) >= 0.55:
        return True
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.85


def dedupe(stories: list[Story]) -> list[Story]:
    """Keep the first story of each cluster of near-duplicate titles, remembering other sources."""
    kept: list[Story] = []
    for s in stories:
        for k in kept:
            if k.url == s.url or similar(k.title, s.title):
                if s.source != k.source and s.source not in k.also:
                    k.also.append(s.source)
                break
        else:
            kept.append(s)
    return kept


def keyword_score(story: Story, topics: list[Topic], feed_topic: str | None) -> None:
    """Cheap relevance 1-10 + matched topics. Used as the fallback when the LLM is unavailable."""
    title, snippet = story.title.lower(), story.snippet.lower()
    total, matched = 0.0, []
    for t in topics:
        hits = sum(2 for k in t.keywords if re.search(rf"\b{re.escape(k)}", title))
        hits += sum(1 for k in t.keywords if re.search(rf"\b{re.escape(k)}", snippet))
        if feed_topic and feed_topic.lower() == t.name.lower():
            hits += 2
        if hits:
            matched.append(t.name)
            total += hits * t.weight
    story.topics = matched
    story.kw_score = max(1.0, min(10.0, 1.5 + total * 1.5)) if matched else 1.0


def rank(
    stories: list[Story],
    prefs: Preferences,
    now: datetime,
    limit: int,
    max_topic_share: float = 0.6,
    weights: dict[str, float] | None = None,
) -> list[Story]:
    """final = relevance (LLM, else keywords) + feedback + freshness + topic-priority bonus."""
    for s in stories:
        base = s.llm_score if s.llm_score is not None else s.kw_score
        src = prefs.source.get(s.source, 0.0)
        topic = max((prefs.topic.get(t, 0.0) for t in s.topics), default=0.0)
        fresh = 0.0
        if s.published:
            age_h = max(0.0, (now - s.published).total_seconds() / 3600)
            fresh = max(0.0, 1.0 - age_h / 24)
        w = max((weights.get(t, 1.0) for t in s.topics), default=1.0) if weights else 1.0
        s.final = base + 2.0 * src + 2.0 * topic + fresh + 2.0 * (w - 1.0)
    ordered = sorted(stories, key=lambda s: s.final, reverse=True)
    cap = max(1, round(limit * max_topic_share))
    picked: list[Story] = []
    counts: dict[str, int] = {}
    for s in ordered:
        key = s.topics[0] if s.topics else "_other"
        if counts.get(key, 0) >= cap:
            continue
        picked.append(s)
        counts[key] = counts.get(key, 0) + 1
        if len(picked) == limit:
            return picked
    for s in ordered:  # relax the diversity cap if we couldn't fill the list
        if s not in picked:
            picked.append(s)
            if len(picked) == limit:
                break
    return picked


def _fallback_summary(snippet: str, title: str) -> str:
    first = re.split(r"(?<=[.!?])\s", snippet.strip(), maxsplit=1)[0] if snippet else ""
    if not first or first.lower().startswith(title.lower()[:30]):
        return ""
    return first if len(first) <= 140 else first[:137].rstrip() + "…"


@register
class NewsSection(SectionPlugin):
    name = "news"
    title = "Top news"
    emoji = "📰"

    def _collect(self, feeds: list[Feed]) -> list[tuple[rss.Entry, Feed]]:
        out: list[tuple[rss.Entry, Feed]] = []
        failed = 0
        for feed in feeds:
            try:
                out.extend((e, feed) for e in rss.fetch_feed(feed))
            except Exception as exc:  # noqa: BLE001 - one bad feed must not sink the rest
                failed += 1
                log.warning("feed failed", extra={"feed": feed.name, "err": str(exc)})
        if feeds and failed == len(feeds):
            raise RuntimeError("all RSS feeds failed")
        return out

    def fetch(self) -> Section:
        ctx, opts = self.ctx, self.options
        limit = int(opts.get("max_items", 5))
        max_age = float(opts.get("max_age_hours", 36))
        n_candidates = int(opts.get("candidates", 15))
        topics = effective_topics(ctx.config, ctx.store)
        feed_topic = {f.name: f.topic for f in ctx.config.feeds}

        stories: list[Story] = []
        for entry, _feed in self._collect(ctx.config.feeds):
            if entry.published and (ctx.now - entry.published).total_seconds() > max_age * 3600:
                continue
            sid = story_id(entry.link, entry.title)
            if ctx.store.was_sent(sid):
                continue
            stories.append(
                Story(
                    sid,
                    entry.title,
                    entry.link,
                    entry.source,
                    entry.summary,
                    entry.published,
                    feed=entry.feed,
                )
            )
        stories = dedupe(stories)
        for s in stories:
            keyword_score(s, topics, feed_topic.get(s.feed))

        weights = {t.name: t.weight for t in topics}

        # Keep the LLM prompt small: only the best keyword/freshness candidates go in.
        prefs = ctx.store.preferences()
        candidates = rank(
            list(stories), prefs, ctx.now, n_candidates, max_topic_share=1.0, weights=weights
        )

        def label(t: Topic) -> str:
            tag = (
                " [TOP PRIORITY]"
                if t.weight >= 1.5
                else " [lower priority]"
                if t.weight < 1
                else ""
            )
            return f"{t.name}{tag} ({', '.join(t.keywords[:6])})"

        interests = "; ".join(label(t) for t in sorted(topics, key=lambda t: -t.weight))
        scored = ctx.ai.score_and_summarize([(s.title, s.snippet) for s in candidates], interests)
        for i, (summary, score) in scored.items():
            candidates[i].summary, candidates[i].llm_score = summary, score
        min_score = float(opts.get("min_score", 3))
        relevant = [
            c
            for c in candidates
            if (c.llm_score if c.llm_score is not None else c.kw_score) >= min_score
        ]
        top = rank(relevant, prefs, ctx.now, limit, weights=weights)

        items = [self._item(n, s, ai=s.llm_score is not None) for n, s in enumerate(top, 1)]
        if not items:
            return self.make(lines=["Nothing new matched your topics."], digest="")
        digest = "Top headlines: " + "; ".join(s.title for s in top[:3])
        return self.make(items=items, digest=digest)

    @staticmethod
    def _item(n: int, s: Story, ai: bool) -> Item:
        summary = s.summary if ai and s.summary else _fallback_summary(s.snippet, s.title)
        also = f" +{len(s.also)}" if s.also else ""
        text = f'<b>{n}.</b> <a href="{html.escape(s.url, quote=True)}">{html.escape(s.title)}</a>'
        if summary:
            text += f"\n{html.escape(summary)}"
        text += f"\n<i>{html.escape(s.source)}{also}</i>"
        meta = {"url": s.url, "title": s.title, "source": s.source, "topics": s.topics}
        return Item(id=s.id, text=text, meta=meta, feedback=True)
