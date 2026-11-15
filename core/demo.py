"""Offline demo mode: canned API responses + a stub LLM so `--demo --dry-run` works with no
network or keys. Fixtures are generated relative to 'now' so dates always look fresh."""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from unittest import mock

import requests

from core.ai import parse_json_array  # noqa: F401  (documents the format the stub must emit)
from core.config import Config
from providers.llm.base import LLMProvider


def _resp(body: str | bytes, status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body.encode() if isinstance(body, str) else body
    r.encoding = "utf-8"
    return r


def _rss(
    now: datetime, items: list[tuple[str, str, str, int]], base: str = "https://example.com/"
) -> str:
    rows = "".join(
        f"<item><title>{t}</title><link>{base}{abs(hash(t)) % 10**6}</link>"
        f"<description>{d}</description><pubDate>{format_datetime(now - timedelta(hours=h))}"
        f"</pubDate></item>"
        for t, d, _, h in items
    )
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{rows}</channel></rss>"


def _ical(now: datetime) -> str:
    def ev(uid: str, title: str, start_h: float, dur_h: float) -> str:
        s = now.replace(hour=int(start_h), minute=int(start_h % 1 * 60), second=0, microsecond=0)
        e = s + timedelta(hours=dur_h)
        f = "%Y%m%dT%H%M%S"
        return (
            f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID={now.tzinfo}:{s:{f}}\n"
            f"DTEND;TZID={now.tzinfo}:{e:{f}}\nSUMMARY:{title}\nEND:VEVENT\n"
        )

    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//demo//EN\n"
        + ev("1", "Team standup", 9.5, 0.5)
        + ev("2", "Design review", 11, 1)
        + ev("3", "1:1 with Priya", 11.5, 0.5)
        + ev("4", "Gym", 18, 1)
        + "END:VCALENDAR\n"
    )


def _fixture(url: str, params: dict[str, Any] | None, now: datetime) -> requests.Response:
    if "open-meteo.com/v1/forecast" in url:
        hours = [f"{now:%Y-%m-%d}T{h:02d}:00" for h in range(24)]
        probs = [0] * 12 + [10, 15, 20, 20, 25, 45, 70, 75, 55, 40, 30, 20]
        return _resp(json.dumps({
            "daily": {"temperature_2m_max": [31.4], "temperature_2m_min": [21.2],
                      "apparent_temperature_max": [34.0], "precipitation_probability_max": [75],
                      "weather_code": [3], "uv_index_max": [8.4], "wind_speed_10m_max": [14.0]},
            "hourly": {"time": hours, "precipitation_probability": probs},
        }))  # fmt: skip
    if "bbci" in url:
        return _resp(_rss(now, [
            ("Arsenal beat Spurs 2-1 in north London derby", "A late winner sealed the derby. Arsenal go top.", "", 5),
            ("Liverpool confirm new striker signing", "The club announced the deal on Saturday.", "", 9),
        ]))  # fmt: skip
    if "theguardian" in url:
        return _resp(_rss(now, [
            ("Arsenal edge Tottenham 2-1 in derby thriller", "Match report from the derby.", "", 6),
            ("Champions League draw: key ties to watch", "Group stage fixtures confirmed.", "", 14),
        ]))  # fmt: skip
    if "verge" in url or "arstechnica" in url or "hnrss" in url or "techcrunch" in url:
        return _resp(_rss(now, [
            ("OpenAI releases new open-weight reasoning model", "The model targets local deployment and tool use.", "", 3),
            ("Nvidia unveils next-gen AI accelerator roadmap", "Roadmap covers the next three GPU generations.", "", 7),
            ("OpenAI launches open-weight reasoning model for developers", "Developers can run the model locally.", "", 4),
            ("Show HN: a tiny SQLite-backed job queue", "Open source and written in Rust.", "", 11),
            ("Local council approves new cycle lanes", "Unrelated local story.", "", 2),
        ]))  # fmt: skip
    if "api.football-data.org" in url:

        def m(days, status, h, a, hg, ag, comp):
            ko = (now + timedelta(days=days)).astimezone(UTC)
            full = {"home": hg, "away": ag}
            return {"utcDate": ko.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status,
                    "competition": {"name": comp},
                    "homeTeam": {"id": h[0], "name": h[1], "shortName": h[1]},
                    "awayTeam": {"id": a[0], "name": a[1], "shortName": a[1]},
                    "score": {"fullTime": full}}  # fmt: skip

        rm, bar, sev = (86, "Real Madrid"), (81, "Barcelona"), (559, "Sevilla")
        return _resp(json.dumps({"matches": [
            m(-8, "FINISHED", rm, (77, "Athletic"), 1, 1, "Primera Division"),
            m(-1, "FINISHED", bar, rm, 1, 2, "Primera Division"),
            m(3, "TIMED", rm, sev, None, None, "Primera Division"),
        ]}))  # fmt: skip
    if "news.google.com" in url:
        return _resp(_rss(now, [
            ("Real Madrid agree deal for young defender - Marca", "", "", 4),
            ("Mbappe double sinks Barcelona in Clasico - AS", "", "", 8),
        ], base="https://news.google.com/rss/articles/"))  # fmt: skip
    if "calendar.example" in url or url.endswith(".ics"):
        return _resp(_ical(now))
    if "finance/chart" in url:
        sym = url.rsplit("/", 1)[-1]
        data = {
            "NVDA": (2.1, 0),
            "%5EGSPC": (-0.4, 0),
            "GC%3DF": (0.9, 0),
            "%5ENSEI": (0.6, 0),
            "%5EBSESN": (0.5, 0),
        }.get(sym, (0.0, 0))
        prev = 100.0
        last = prev * (1 + data[0] / 100)
        t = int((now - timedelta(days=1)).timestamp())
        return _resp(json.dumps({"chart": {"result": [{
            "meta": {"regularMarketPrice": last, "exchangeTimezoneName": "America/New_York"},
            "timestamp": [t - 86400, t],
            "indicators": {"quote": [{"close": [prev, last]}]},
        }]}}))  # fmt: skip
    if "coingecko" in url:
        return _resp(json.dumps({
            "bitcoin": {"usd_24h_change": 3.2}, "ethereum": {"usd_24h_change": -1.1}}))  # fmt: skip
    if "screener" in url:
        scr = (params or {}).get("scrIds", "")
        rows = (
            [("SMCI", "Super Micro Computer", 14.2), ("PLTR", "Palantir", 8.1)]
            if scr == "day_gainers"
            else [("INTC", "Intel", -9.4), ("NKE", "Nike", -6.2)]
        )
        quotes = [{"symbol": s, "shortName": n, "regularMarketChangePercent": c,
                   "marketCap": 5e10, "regularMarketPrice": 50.0} for s, n, c in rows]  # fmt: skip
        return _resp(json.dumps({"finance": {"result": [{"quotes": quotes}]}}))
    return _resp("not found", 404)


class DemoLLM(LLMProvider):
    """Stub that answers the three prompt kinds so the AI path is exercised offline."""

    name = "demo"

    def complete(self, prompt: str, *, max_tokens: int = 512, json_mode: bool = False) -> str:
        if "[task:news]" in prompt:
            rows = []
            for m in re.finditer(r"^(\d+)\. (.*?) \|", prompt, flags=re.MULTILINE):
                title = m.group(2)
                score = (
                    9
                    if re.search(r"openai|nvidia|arsenal|real madrid|mbappe|ai\b", title, re.I)
                    else 2
                )
                rows.append({"i": int(m.group(1)), "s": f"Summary: {title[:70]}.", "r": score})
            return json.dumps(rows)
        if "[task:intro]" in prompt:
            return (
                "It's a warm, humid day with rain likely by the evening, so plan to be home "
                "before it starts. You have a busy morning, with two meetings that overlap."
            )
        return "Use `git switch -` to jump back to your previous branch instantly."


def install(cfg: Config, now: datetime) -> mock._patch:
    """Patch the HTTP layer with fixtures and point config at demo-friendly inputs."""
    real = requests.request

    def fake(method: str, url: str, **kw: Any) -> requests.Response:
        if "api.telegram.org" in url:
            return real(method, url, **kw)
        return _fixture(url, kw.get("params"), now)

    tasks = Path(tempfile.mkdtemp()) / "tasks.md"
    d = now.date()
    tasks.write_text(
        f"- [ ] Pay electricity bill 📅 {d - timedelta(days=2)}\n"
        f"- [ ] Send quarterly report due:{d}\n"
        f"- [ ] Book dentist @due({d})\n"
        f"- [ ] Plan weekend trip 📅 {d + timedelta(days=3)}\n"
        "- [x] Renew passport\n",
        encoding="utf-8",
    )
    cfg.tasks_file = str(tasks)
    import os

    os.environ["FOOTBALL_DATA_API_KEY"] = "demo-key"
    os.environ["CALENDAR_ICAL_URL"] = "https://calendar.example/private/basic.ics"
    patcher = mock.patch("requests.request", fake)
    patcher.start()
    return patcher
