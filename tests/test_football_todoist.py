from datetime import date, timedelta

import pytest

from bot.commands import handle_text
from core.store import Preferences
from providers import football as fb
from providers import rss, todoist
from sections import football as fs
from sections import tasks as tk
from sections.news import Story, rank
from tests.conftest import NOW


def match(days, status, home, away, hg=None, ag=None, comp="La Liga"):
    return {
        "utcDate": (NOW + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "competition": {"name": comp},
        "homeTeam": {"id": home[0], "name": home[1], "shortName": home[1]},
        "awayTeam": {"id": away[0], "name": away[1], "shortName": away[1]},
        "score": {"fullTime": {"home": hg, "away": ag}},
    }


RM, BAR, SEV = (86, "Real Madrid"), (81, "Barcelona"), (559, "Sevilla")


def patch_api(monkeypatch, matches):
    seen = {}

    def fake(url, **kw):
        seen.update(url=url, **kw)
        return {"matches": matches}

    monkeypatch.setattr(fb.http, "get_json", fake)
    monkeypatch.setenv("FOOTBALL_DATA_API_KEY", "KEY")
    return seen


def test_result_perspective_win_loss_draw():
    away_win = fb.Match("L", "Barca", "Real", "FINISHED", NOW, 1, 2, team_is_home=False)
    home_loss = fb.Match("L", "Real", "Barca", "FINISHED", NOW, 0, 3, team_is_home=True)
    draw = fb.Match("L", "Real", "Barca", "FINISHED", NOW, 1, 1, team_is_home=True)
    assert [m.result_for_team() for m in (away_win, home_loss, draw)] == ["W", "L", "D"]
    assert fb.Match("L", "a", "b", "TIMED", NOW, None, None, True).score == "vs"


def test_section_last_result_next_fixture_and_auth(monkeypatch, make_ctx):
    seen = patch_api(monkeypatch, [
        match(-1, "FINISHED", BAR, RM, 1, 2),
        match(-8, "FINISHED", RM, SEV, 0, 0),
        match(3, "TIMED", RM, SEV),
        match(10, "SCHEDULED", BAR, RM),
    ])  # fmt: skip
    s = fs.FootballSection(make_ctx(), {}).fetch()
    text = "\n".join(s.lines)
    assert (
        "✅ W Barcelona 1–2 Real Madrid" in text
    )  # most recent finished match, not the older draw
    assert "Next: vs Sevilla" in text  # earliest upcoming, played at home -> 'vs'
    assert seen["headers"]["X-Auth-Token"] == "KEY" and "/teams/86/" in seen["url"]
    assert set(seen["params"]) == {"dateFrom", "dateTo"}


def test_section_shows_live_match_and_away_fixture(monkeypatch, make_ctx):
    patch_api(monkeypatch, [match(0, "IN_PLAY", RM, BAR, 1, 0), match(7, "TIMED", SEV, RM)])
    text = "\n".join(fs.FootballSection(make_ctx(), {}).fetch().lines)
    assert "LIVE" in text and "Real Madrid 1–0 Barcelona" in text
    assert "Next: at Sevilla" in text


def test_section_handles_no_matches_and_missing_key(monkeypatch, make_ctx):
    patch_api(monkeypatch, [])
    assert "No recent" in fs.FootballSection(make_ctx(), {}).fetch().lines[0]
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY")
    with pytest.raises(RuntimeError):
        fs.FootballSection(make_ctx(), {}).fetch()


def test_score_command_sends_section_without_greeting(monkeypatch, make_ctx, tg, config):
    patch_api(monkeypatch, [match(-1, "FINISHED", BAR, RM, 1, 2)])
    config.sections["football"] = {"enabled": True}
    handle_text(make_ctx(), tg, "/score")
    assert "Barcelona 1–2 Real Madrid" in tg.texts and "Good morning" not in tg.texts


def test_score_command_when_section_disabled(make_ctx, tg):
    handle_text(make_ctx(), tg, "/score")
    assert "switched off" in tg.texts


def test_google_news_titles_split_into_headline_and_publisher():
    xml = (
        "<rss><channel><item><title>Madrid sign defender - Marca</title>"
        "<link>https://news.google.com/rss/articles/abc</link><description>&lt;ol&gt;junk&lt;/ol&gt;"
        "</description></item></channel></rss>"
    )
    e = rss.parse_feed(xml, "Real Madrid news")[0]
    assert (e.title, e.source, e.feed, e.summary) == (
        "Madrid sign defender", "Marca", "Real Madrid news", "",
    )  # fmt: skip


def test_topic_weight_boosts_priority_topic():
    def story(title, topic):
        s = Story(title, title, f"https://x/{title}", "S", "", NOW - timedelta(hours=3))
        s.topics, s.llm_score = [topic], 7
        return s

    rm, generic = story("rm", "Real Madrid"), story("gen", "Football")
    out = rank([generic, rm], Preferences(), NOW, 2, weights={"Real Madrid": 1.5, "Football": 0.7})
    assert out[0] is rm and rm.final > generic.final + 1.5


def test_todoist_parses_paginates_and_skips_checked(monkeypatch):
    pages = iter([
        {"results": [{"content": "Pay rent", "due": {"date": "2026-11-10"}},
                     {"content": "Done", "checked": True, "due": {"date": "2026-11-10"}}],
         "next_cursor": "c2"},
        {"results": [{"content": "Call bank", "due": {"date": "2026-11-15T09:00:00"}},
                     {"content": "No due"}], "next_cursor": None},
    ])  # fmt: skip
    calls = []

    def fake(url, **kw):
        calls.append(kw)
        return next(pages)

    monkeypatch.setattr(todoist.http, "get_json", fake)
    out = todoist.fetch_due("TOK")
    assert out == [
        ("Pay rent", date(2026, 11, 10)),
        ("Call bank", date(2026, 11, 15)),
        ("No due", None),
    ]
    assert (
        calls[0]["headers"]["Authorization"] == "Bearer TOK"
        and calls[1]["params"]["cursor"] == "c2"
    )


def test_tasks_merge_file_and_todoist_without_duplicates(monkeypatch, tmp_path, make_ctx):
    f = tmp_path / "t.md"
    f.write_text(
        "- [ ] Pay rent 📅 2026-11-10\n- [ ] File report due:2026-11-15\n", encoding="utf-8"
    )
    monkeypatch.setenv("TODOIST_API_TOKEN", "TOK")
    monkeypatch.setattr(
        tk.todoist, "fetch_due",
        lambda tok: [("pay rent", date(2026, 11, 10)), ("Call bank", date(2026, 11, 15))],
    )  # fmt: skip
    s = tk.TasksSection(make_ctx(), {"file": str(f)}).fetch()
    text = "\n".join(s.lines)
    assert text.count("ay rent") == 1 and "Call bank" in text and "File report" in text
    assert s.digest == "tasks: 1 overdue, 2 due today"


def test_todoist_outage_does_not_hide_file_tasks(monkeypatch, tmp_path, make_ctx):
    f = tmp_path / "t.md"
    f.write_text("- [ ] File report due:2026-11-15\n", encoding="utf-8")
    monkeypatch.setenv("TODOIST_API_TOKEN", "TOK")

    def boom(tok):
        raise RuntimeError("todoist down")

    monkeypatch.setattr(tk.todoist, "fetch_due", boom)
    assert "File report" in "\n".join(tk.TasksSection(make_ctx(), {"file": str(f)}).fetch().lines)
    monkeypatch.setattr(tk.todoist, "fetch_due", boom)
    with pytest.raises(RuntimeError):  # no file AND todoist down -> surface the failure
        tk.TasksSection(make_ctx(), {"file": str(tmp_path / "none.md")}).fetch()


def test_todoist_only_works_without_tasks_file(monkeypatch, tmp_path, make_ctx):
    monkeypatch.setenv("TODOIST_API_TOKEN", "TOK")
    monkeypatch.setattr(
        tk.todoist, "fetch_due", lambda tok: [("Only in Todoist", date(2026, 11, 15))]
    )
    s = tk.TasksSection(make_ctx(), {"file": str(tmp_path / "none.md")}).fetch()
    assert "Only in Todoist" in "\n".join(s.lines)
