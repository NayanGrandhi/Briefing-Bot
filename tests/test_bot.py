from bot.commands import handle_callback, handle_text
from bot.poller import poll
from core import runner
from core.topics import effective_topics

ITEM = {"url": "https://x.test/1", "title": "Story", "source": "Src", "topics": ["Football"]}


def cb(data, cid="c1", mid=7):
    return {"id": cid, "data": data, "message": {"message_id": mid, "chat": {"id": 42}}}


def test_vote_toggle_and_button_state(make_ctx, tg, store):
    store.remember_item("abc", ITEM)
    ctx = make_ctx()
    handle_callback(ctx, tg, cb("fb:u:abc"))
    assert store.feedback_state("abc") == ("up", False)
    assert tg.edits[-1][1][0][0].label == "👍 ✓"
    handle_callback(ctx, tg, cb("fb:d:abc"))  # switching vote replaces it
    assert store.feedback_state("abc") == ("down", False)
    handle_callback(ctx, tg, cb("fb:d:abc"))  # pressing again clears it
    assert store.feedback_state("abc") == (None, False)


def test_save_and_saved_command(make_ctx, tg, store):
    store.remember_item("abc", ITEM)
    ctx = make_ctx()
    handle_callback(ctx, tg, cb("fb:s:abc"))
    assert store.feedback_state("abc")[1] is True
    handle_text(ctx, tg, "/saved")
    assert "Story" in tg.sent[-1].text


def test_unknown_or_malformed_callbacks_are_safe(make_ctx, tg):
    ctx = make_ctx()
    handle_callback(ctx, tg, cb("garbage"))
    handle_callback(ctx, tg, cb("fb:u:doesnotexist"))
    assert "too old" in tg.answers[-1][1]


def test_feedback_changes_preferences(make_ctx, tg, store):
    store.remember_item("abc", ITEM)
    for _ in range(3):
        store.set_vote("abc", "down")
        store.set_vote("abc", "up")
    store.toggle_save("abc")
    p = store.preferences()
    assert p.source["Src"] > 0.5 and p.topic["Football"] > 0.5


def test_skip_sets_tomorrow_and_cancel(make_ctx, tg, store):
    ctx = make_ctx()
    assert handle_text(ctx, tg, "/skip") is False
    assert store.get_state("skip_date") == "2026-11-16"
    handle_text(ctx, tg, "/skip cancel")
    assert store.get_state("skip_date") is None
    assert runner.tomorrow(ctx.now) == "2026-11-16"


def test_now_requests_a_briefing(make_ctx, tg):
    assert handle_text(make_ctx(), tg, "/now") is True
    assert handle_text(make_ctx(), tg, "/now@my_bot") is True


def test_topics_add_remove_reset(make_ctx, tg, store, config):
    ctx = make_ctx()
    handle_text(ctx, tg, "/topics add Cricket: cricket, ipl, bcci")
    names = [t.name for t in effective_topics(config, store)]
    assert names == ["Tech & AI", "Football", "Cricket"]
    handle_text(ctx, tg, "/topics remove football")
    assert [t.name for t in effective_topics(config, store)] == ["Tech & AI", "Cricket"]
    handle_text(ctx, tg, "/topics remove Nope")
    assert "No topic named" in tg.sent[-1].text
    handle_text(ctx, tg, "/topics")
    assert "Cricket" in tg.sent[-1].text
    handle_text(ctx, tg, "/topics reset")
    assert [t.name for t in effective_topics(config, store)] == ["Tech & AI", "Football"]


def test_unknown_command_shows_help(make_ctx, tg):
    handle_text(make_ctx(), tg, "/wat")
    assert "/now" in tg.sent[-1].text


def test_poll_processes_updates_once_and_persists_offset(make_ctx, tg, store):
    store.remember_item("abc", ITEM)
    tg.updates = [
        {"update_id": 10, "callback_query": cb("fb:u:abc")},
        {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/skip"}},
    ]
    ctx = make_ctx()
    assert poll(ctx, tg) == 2
    assert store.get_state("tg_offset") == "12"
    assert poll(ctx, tg) == 0  # nothing re-processed


def test_poll_ignores_strangers(make_ctx, tg, store):
    tg.updates = [{"update_id": 1, "message": {"chat": {"id": 999}, "text": "/skip"}},
                  {"update_id": 2, "callback_query": {"id": "x", "data": "fb:u:abc",
                   "message": {"message_id": 1, "chat": {"id": 999}}}}]  # fmt: skip
    assert poll(make_ctx(), tg) == 0
    assert store.get_state("skip_date") is None and store.get_state("tg_offset") == "3"


def test_poll_survives_a_bad_update_and_runs_now(make_ctx, tg, store, monkeypatch, config):
    config.sections = {}
    tg.updates = [
        {
            "update_id": 1,
            "callback_query": {"id": "z", "data": "fb:u:abc"},
        },  # malformed: no message
        {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/now"}},
    ]
    sent = []
    monkeypatch.setattr("bot.poller.run_briefing", lambda ctx, tg, **k: sent.append(k))
    poll(make_ctx(), tg)
    assert sent == [{"force": True}] and store.get_state("tg_offset") == "3"


def test_health_alert_fires_once_when_briefing_missing(make_ctx, tg, store):
    from datetime import timedelta

    from bot.poller import check_health

    ctx = make_ctx()
    ctx.now = ctx.now + timedelta(hours=3)  # 10:00, well past send time + 90 min
    assert check_health(ctx, tg) is False  # fresh install: nothing sent yet, stay quiet
    store.set_state("last_sent_date", "2026-11-14")
    assert check_health(ctx, tg) is True and "No briefing" in tg.sent[-1].text
    assert check_health(ctx, tg) is False  # only once per day
    store.set_state("health_alerted", None)
    store.set_state("skipped_date", "2026-11-15")
    assert check_health(ctx, tg) is False  # skipped on purpose
