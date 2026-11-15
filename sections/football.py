"""Team section (default: Real Madrid): live status, last result, next fixture."""

from __future__ import annotations

import html
import os
from datetime import timedelta

from core.models import Section
from providers import football
from providers.football import Match
from sections.base import SectionPlugin, register

_ICON = {"W": "✅ W", "D": "➖ D", "L": "❌ L"}


@register
class FootballSection(SectionPlugin):
    name = "football"
    title = "Real Madrid"
    emoji = "⚪"

    def _fmt(self, m: Match) -> str:
        ko = m.kickoff.astimezone(self.ctx.config.tz)
        return (
            f"{html.escape(m.home)} {m.score} {html.escape(m.away)}",
            f"{ko:%a %d %b}",
            f"{ko:%H:%M}",
        )

    def fetch(self) -> Section:
        token = os.environ.get("FOOTBALL_DATA_API_KEY")
        if not token:
            raise RuntimeError("FOOTBALL_DATA_API_KEY is not set")
        team_id = int(self.options.get("team_id", 86))
        name = str(self.options.get("team_name", "Real Madrid"))
        today = self.ctx.today
        matches = football.get_matches(
            team_id, token, today - timedelta(days=10), today + timedelta(days=21)
        )

        live = [m for m in matches if m.status in football.LIVE]
        finished = [m for m in matches if m.status == "FINISHED"]
        upcoming = [m for m in matches if m.status in football.UPCOMING]

        lines: list[str] = []
        digest_bits: list[str] = []
        for m in live:
            teams, _, _ = self._fmt(m)
            lines.append(f"🔴 <b>LIVE</b> {teams} <i>({html.escape(m.competition)})</i>")
        if finished:
            m = finished[-1]
            teams, day, _ = self._fmt(m)
            res = _ICON.get(m.result_for_team() or "", "")
            lines.append(f"{res} {teams} <i>({html.escape(m.competition)}, {day})</i>")
            digest_bits.append(f"{name}'s last result: {m.home} {m.score} {m.away}")
        if upcoming:
            m = upcoming[0]
            ko = m.kickoff.astimezone(self.ctx.config.tz)
            venue = "vs" if m.team_is_home else "at"
            opp = m.away if m.team_is_home else m.home
            lines.append(
                f"⏭ Next: {venue} {html.escape(opp)} · {html.escape(m.competition)} · "
                f"<b>{ko:%a %d %b, %H:%M}</b>"
            )
            if ko.date() == today:
                digest_bits.append(f"{name} play {opp} today at {ko:%H:%M}")
        if not lines:
            lines = [f"No recent or upcoming {html.escape(name)} matches found."]
        return self.make(title=name, lines=lines, digest="; ".join(digest_bits))
