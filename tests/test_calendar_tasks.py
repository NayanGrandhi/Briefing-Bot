from datetime import date

from sections import calendar as cal
from sections import tasks as tk
from tests.conftest import IST


def ics(*events):
    return "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\n" + "".join(events) + "END:VCALENDAR\n"


def ev(uid, title, start, end, extra=""):
    return (f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID=Asia/Kolkata:{start}\n"
            f"DTEND;TZID=Asia/Kolkata:{end}\nSUMMARY:{title}\n{extra}END:VEVENT\n")  # fmt: skip


DAY = date(2026, 11, 15)


def test_events_sorted_conflicts_and_cancelled():
    text = ics(
        ev("1", "Late", "20261115T150000", "20261115T160000"),
        ev("2", "Early", "20261115T090000", "20261115T100000"),
        ev("3", "Overlap", "20261115T093000", "20261115T103000"),
        ev("4", "Gone", "20261115T110000", "20261115T120000", "STATUS:CANCELLED\n"),
        ev("5", "Other day", "20261116T090000", "20261116T100000"),
    )
    events = cal.parse_events(text, DAY, IST)
    assert [e.title for e in events] == ["Early", "Overlap", "Late"]
    pairs = cal.find_conflicts(events)
    assert [(a.title, b.title) for a, b in pairs] == [("Early", "Overlap")]


def test_back_to_back_events_do_not_conflict():
    text = ics(ev("1", "A", "20261115T090000", "20261115T100000"),
               ev("2", "B", "20261115T100000", "20261115T110000"))  # fmt: skip
    assert cal.find_conflicts(cal.parse_events(text, DAY, IST)) == []


def test_all_day_and_recurring_events():
    text = ics(
        "BEGIN:VEVENT\nUID:a\nDTSTART;VALUE=DATE:20261115\nSUMMARY:Holiday\nEND:VEVENT\n",
        "BEGIN:VEVENT\nUID:r\nDTSTART;TZID=Asia/Kolkata:20261101T180000\n"
        "DTEND;TZID=Asia/Kolkata:20261101T190000\nRRULE:FREQ=WEEKLY;BYDAY=SU\n"
        "SUMMARY:Weekly Sunday\nEND:VEVENT\n",
    )
    events = cal.parse_events(text, DAY, IST)
    assert {e.title for e in events} == {"Holiday", "Weekly Sunday"}
    assert next(e for e in events if e.title == "Holiday").all_day


def test_section_highlights_first_event_and_flags_conflict(monkeypatch, make_ctx):
    text = ics(ev("1", "Standup", "20261115T093000", "20261115T100000"),
               ev("2", "Review", "20261115T094500", "20261115T104500"))  # fmt: skip
    monkeypatch.setenv("CALENDAR_ICAL_URL", "https://cal.test/secret.ics")
    monkeypatch.setattr(cal.http, "get_text", lambda *a, **k: text)
    s = cal.CalendarSection(make_ctx(), {}).fetch()
    assert s.lines[0].startswith("⭐") and "first up" in s.lines[0]
    assert any("Conflict" in line and "09:45–10:00" in line for line in s.lines)
    assert "first at 09:30" in s.digest


def test_empty_day_and_missing_url(monkeypatch, make_ctx):
    import pytest

    monkeypatch.setenv("CALENDAR_ICAL_URL", "https://cal.test/x.ics")
    monkeypatch.setattr(cal.http, "get_text", lambda *a, **k: ics())
    assert "clear day" in cal.CalendarSection(make_ctx(), {}).fetch().lines[0]
    monkeypatch.delenv("CALENDAR_ICAL_URL")
    with pytest.raises(RuntimeError):
        cal.CalendarSection(make_ctx(), {}).fetch()


MD = """# Tasks
- [ ] Pay rent 📅 2026-11-10
- [ ] Call bank due:2026-11-15
- [ ] File report @due(2026-11-15)
- [x] Done thing 📅 2026-11-01
- [ ] Future 📅 2026-12-01
- [ ] No date at all
* [ ] Star bullet 📅 2026-11-14
- [ ] Bad date 📅 2026-13-45
"""


def test_parse_tasks_formats():
    t = {x.text: x for x in tk.parse_tasks(MD)}
    assert t["Pay rent"].due == date(2026, 11, 10)
    assert t["Call bank"].due == date(2026, 11, 15) and t["File report"].due == DAY
    assert t["Done thing"].done and t["No date at all"].due is None
    assert t["Star bullet"].due == date(2026, 11, 14) and t["Bad date"].due is None


def test_tasks_section_overdue_and_due_today(tmp_path, make_ctx):
    f = tmp_path / "tasks.md"
    f.write_text(MD, encoding="utf-8")
    s = tk.TasksSection(make_ctx(), {"file": str(f)}).fetch()
    text = "\n".join(s.lines)
    assert "Overdue (2)" in text and "5d overdue" in text and "Due today (2)" in text
    assert "Future" not in text and "Done thing" not in text
    assert s.digest == "tasks: 2 overdue, 2 due today"


def test_tasks_missing_file_and_nothing_due(tmp_path, make_ctx):
    import pytest

    with pytest.raises(FileNotFoundError):
        tk.TasksSection(make_ctx(), {"file": str(tmp_path / "nope.md")}).fetch()
    f = tmp_path / "t.md"
    f.write_text("- [ ] later 📅 2030-01-01\n", encoding="utf-8")
    assert "Nothing due" in tk.TasksSection(make_ctx(), {"file": str(f)}).fetch().lines[0]
