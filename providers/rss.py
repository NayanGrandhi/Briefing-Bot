"""RSS 2.0 / Atom parsing with defusedxml (feeds are untrusted input)."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from defusedxml import ElementTree as ET

from core import http
from core.config import Feed

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Entry:
    title: str
    link: str
    summary: str
    published: datetime | None
    source: str
    feed: str = ""  # name of the configured feed it came from (source may be the publisher)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _clean(text: str | None) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", text or "")).split())


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_feed(xml_text: str, source: str) -> list[Entry]:
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    entries: list[Entry] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        for child in node:
            name = _local(child.tag)
            if name == "link":
                fields.setdefault("link", child.get("href") or (child.text or ""))
            elif name in ("title", "description", "summary", "content", "encoded"):
                fields.setdefault("summary" if name != "title" else "title", child.text or "")
            elif name in ("pubDate", "published", "updated", "date"):
                fields.setdefault("date", child.text or "")
        title, link = _clean(fields.get("title")), fields.get("link", "").strip()
        summary, src = _clean(fields.get("summary")), source
        if "news.google.com" in link and " - " in title:
            # Google News: "Headline - Publisher"; its description is only a list of related links.
            title, _, publisher = title.rpartition(" - ")
            summary, src = "", publisher.strip() or source
        if title and link:
            entries.append(
                Entry(title, link, summary, _parse_date(fields.get("date")), src, source)
            )
    return entries


def fetch_feed(feed: Feed) -> list[Entry]:
    return parse_feed(http.get_text(feed.url, timeout=10, retries=2), feed.name)
