"""Calendar: today's events from a secret iCal URL; flags conflicts, highlights the first event."""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar

from core import http
from core.models import Section
from sections.base import SectionPlugin, register


@dataclass
class Event:
    title: str
    start: datetime | None  # None for all-day
    end: datetime | None
    location: str | None = None

    @property
    def all_day(self) -> bool:
        return self.start is None


def _aware(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)


def parse_events(ical_text: str, day: date, tz: ZoneInfo) -> list[Event]:
    cal = Calendar.from_ical(ical_text)
    events: list[Event] = []
    for comp in recurring_ical_events.of(cal).at(day):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        title = str(comp.get("SUMMARY", "(no title)"))
        loc = str(comp["LOCATION"]) if comp.get("LOCATION") else None
        start_prop = comp.get("DTSTART")
        if start_prop is None:
            continue
        start = start_prop.dt
        if not isinstance(start, datetime):  # all-day event
            events.append(Event(title, None, None, loc))
            continue
        end_prop = comp.get("DTEND")
        end = end_prop.dt if end_prop is not None else start
        if not isinstance(end, datetime):
            end = start
        events.append(Event(title, _aware(start, tz), _aware(end, tz), loc))
    return sorted(
        events,
        key=lambda e: (e.start is not None, e.start or datetime.combine(day, time.min, tz)),
    )


def find_conflicts(events: list[Event]) -> list[tuple[Event, Event]]:
    timed = [e for e in events if e.start and e.end]
    pairs = []
    for i, a in enumerate(timed):
        for b in timed[i + 1 :]:
            if a.start < b.end and b.start < a.end:  # type: ignore[operator]
                pairs.append((a, b))
    return pairs


def _span(e: Event) -> str:
    assert e.start and e.end
    return f"{e.start:%H:%M}–{e.end:%H:%M}" if e.end > e.start else f"{e.start:%H:%M}"


@register
class CalendarSection(SectionPlugin):
    name = "calendar"
    title = "Today's schedule"
    emoji = "📅"

    def fetch(self) -> Section:
        url = os.environ.get("CALENDAR_ICAL_URL")
        if not url:
            raise RuntimeError("CALENDAR_ICAL_URL is not set")
        tz = self.ctx.config.tz
        events = parse_events(http.get_text(url, timeout=15), self.ctx.today, tz)
        if not events:
            return self.make(lines=["Nothing scheduled — a clear day."], digest="no events today")

        limit = int(self.options.get("max_events", 8))
        timed = [e for e in events if e.start]
        first = timed[0] if timed else None
        lines: list[str] = []
        for e in [x for x in events if x.all_day]:
            lines.append(f"🗓 All day: {html.escape(e.title)}")
        for e in timed[:limit]:
            star = "⭐ " if e is first else ""
            where = f" 📍{html.escape(e.location)}" if e.location else ""
            row = f"{_span(e)} {html.escape(e.title)}{where}"
            lines.append(f"{star}<b>{row}</b> ← first up" if e is first else row)
        if len(timed) > limit:
            lines.append(f"<i>+{len(timed) - limit} more</i>")
        for a, b in find_conflicts(events):
            lo = max(a.start, b.start)  # type: ignore[type-var]
            hi = min(a.end, b.end)  # type: ignore[type-var]
            lines.append(
                f"⚠️ Conflict: {html.escape(a.title)} ↔ {html.escape(b.title)} "
                f"({lo:%H:%M}–{hi:%H:%M})"
            )
        digest = f"{len(events)} event{'s' if len(events) != 1 else ''} today"
        if first:
            digest += f", first at {first.start:%H:%M}"
        n_conf = len(find_conflicts(events))
        if n_conf:
            digest += f", with {n_conf} scheduling conflict{'s' if n_conf != 1 else ''}"
        return self.make(lines=lines, digest=digest)
