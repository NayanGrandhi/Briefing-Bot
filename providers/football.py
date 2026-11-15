"""football-data.org v4 (free tier: 12 competitions incl. La Liga + Champions League)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from core import http

API = "https://api.football-data.org/v4/teams/{team_id}/matches"
LIVE = {"IN_PLAY", "PAUSED", "LIVE"}
UPCOMING = {"SCHEDULED", "TIMED"}


@dataclass
class Match:
    competition: str
    home: str
    away: str
    status: str
    kickoff: datetime  # UTC, aware
    home_goals: int | None
    away_goals: int | None
    team_is_home: bool

    @property
    def score(self) -> str:
        if self.home_goals is None or self.away_goals is None:
            return "vs"
        return f"{self.home_goals}–{self.away_goals}"

    def result_for_team(self) -> str | None:
        if self.home_goals is None or self.away_goals is None:
            return None
        mine, theirs = (
            (self.home_goals, self.away_goals)
            if self.team_is_home
            else (self.away_goals, self.home_goals)
        )
        return "W" if mine > theirs else "L" if mine < theirs else "D"


def get_matches(team_id: int, token: str, date_from: date, date_to: date) -> list[Match]:
    data = http.get_json(
        API.format(team_id=team_id),
        params={"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
        headers={"X-Auth-Token": token},
        timeout=10,
        retries=2,
    )
    out: list[Match] = []
    for m in data.get("matches", []):
        full = (m.get("score") or {}).get("fullTime") or {}
        out.append(
            Match(
                competition=(m.get("competition") or {}).get("name", ""),
                home=m["homeTeam"].get("shortName") or m["homeTeam"]["name"],
                away=m["awayTeam"].get("shortName") or m["awayTeam"]["name"],
                status=m.get("status", ""),
                kickoff=datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")),
                home_goals=full.get("home"),
                away_goals=full.get("away"),
                team_is_home=m["homeTeam"].get("id") == team_id,
            )
        )
    return sorted(out, key=lambda x: x.kickoff)
