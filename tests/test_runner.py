import pytest

from core import runner
from core.models import Section
from providers import telegram as tgmod
from sections import REGISTRY, SectionPlugin, register
from tests.conftest import FakeLLM


@pytest.fixture
def plugins(config):
    @register
    class Good(SectionPlugin):
        name, title, emoji = "good", "Good", "✅"

        def fetch(self):
            return self.make(lines=["all fine"], digest="good works")

    @register
    class Bad(SectionPlugin):
        name, title, emoji = "bad", "Bad", "💥"

        def fetch(self):
            raise RuntimeError("kaboom token=123456:ABCDEFGHIJKLMNOP")

    config.sections = {"good": {"enabled": True}, "bad": {"enabled": True}}
    yield
    REGISTRY.pop("good", None)
    REGISTRY.pop("bad", None)


def test_a_broken_section_does_not_block_the_rest(make_ctx, plugins):
    sections = runner.fetch_sections(make_ctx())
    by = {s.name: s for s in sections}
    assert by["good"].ok and not by["bad"].ok
    assert "kaboom" in by["bad"].error


def test_briefing_is_sent_with_failed_section_noted(make_ctx, plugins, tg, store):
    n = runner.run_briefing(
        make_ctx(FakeLLM("Everything is working today. Enjoy your morning here.")), tg
    )
    assert n >= 1
    assert "all fine" in tg.texts and "Bad</b> unavailable" in tg.texts
    assert "Everything is working today" in tg.texts  # AI intro used
    assert store.get_state("last_sent_date") == "2026-11-15"


def test_non_ai_fallback_intro_when_llm_down(make_ctx, plugins, tg):
    runner.run_briefing(make_ctx(FakeLLM(Exception("x"))), tg)
    assert "Today: good works." in tg.texts


def test_never_fails_to_send_even_if_build_explodes(make_ctx, tg, monkeypatch):
    monkeypatch.setattr(runner, "build_briefing", lambda *a, **k: 1 / 0)
    runner.run_briefing(make_ctx(), tg)
    assert "couldn't build" in tg.texts


def test_skip_and_already_sent_gates(make_ctx, plugins, tg, store):
    store.set_state("skip_date", "2026-11-15")
    assert runner.run_briefing(make_ctx(), tg) == 0 and not tg.sent
    assert store.get_state("skip_date") is None  # skip is one-shot
    assert runner.run_briefing(make_ctx(), tg) > 0
    tg.sent.clear()
    assert runner.run_briefing(make_ctx(), tg) == 0  # no double send
    assert runner.run_briefing(make_ctx(), tg, force=True) > 0  # /now overrides


def test_dry_run_prints_and_sends_nothing(make_ctx, plugins, tg, store, capsys):
    runner.run_briefing(make_ctx(), tg, dry_run=True)
    assert "all fine" in capsys.readouterr().out
    assert not tg.sent and store.get_state("last_sent_date") is None


def test_section_timeout(make_ctx, config, monkeypatch):
    import threading
    import time

    release = threading.Event()

    @register
    class Slow(SectionPlugin):
        name, title, emoji = "slow", "Slow", "🐢"

        def fetch(self):
            release.wait(5)
            return self.make()

    config.sections = {"slow": {"enabled": True}}
    monkeypatch.setattr(runner, "SECTION_TIMEOUT", 0.2)
    try:
        s = runner.fetch_sections(make_ctx())
        assert s[0].error == "timed out"
    finally:
        release.set()
        time.sleep(0.2)  # let the worker thread finish before capture closes
        REGISTRY.pop("slow", None)


def test_items_are_persisted_so_callbacks_can_resolve_them(make_ctx, config, tg, store):
    from core.models import Item

    @register
    class Itemy(SectionPlugin):
        name, title, emoji = "itemy", "Itemy", "📰"

        def fetch(self):
            meta = {"url": "https://x.test/1", "title": "T", "source": "S", "topics": ["Tech"]}
            return self.make(items=[Item("abc123", "<b>1.</b> T", meta, feedback=True)])

    config.sections = {"itemy": {"enabled": True}}
    try:
        runner.run_briefing(make_ctx(), tg)
    finally:
        REGISTRY.pop("itemy", None)
    assert store.get_item("abc123")["source"] == "S"
    assert tg.sent[-1].buttons[0][0].data == "fb:u:abc123"


def test_split_text_and_plain_fallback(monkeypatch):
    long = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    parts = tgmod.split_text(long, 500)
    assert all(len(p) <= 500 for p in parts) and "\n".join(parts) == long

    client = tgmod.TelegramClient("tok", "1")
    calls = []

    def fake(method, payload=None, timeout=15):
        calls.append(payload)
        if "parse_mode" in payload:
            raise tgmod.http.HTTPError(
                "bad", 400, '{"description": "Bad Request: can\'t parse entities"}'
            )

    monkeypatch.setattr(client, "_call", fake)
    client.send(tgmod.Message(text="<b>Hi</b> &amp; bye <a href='x'>l</a>"))
    assert calls[-1]["text"] == "Hi & bye l" and "parse_mode" not in calls[-1]


def test_alert_never_raises(monkeypatch):
    client = tgmod.TelegramClient("tok", "1")

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(client, "_call", boom)
    assert client.alert("x") is False


def test_failed_section_shape():
    s = Section.failed("n", "T", "E", "err")
    assert not s.ok and s.error == "err"


def test_telegram_call_builds_correct_request_and_checks_ok(monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen.update(url=url, **kw)
        return {"ok": True, "result": [{"update_id": 1}]}

    monkeypatch.setattr(tgmod.http, "post_json", fake_post)
    client = tgmod.TelegramClient("TOK:EN", "99")
    assert client.get_updates(5) == [{"update_id": 1}]
    assert seen["url"].endswith("/botTOK:EN/getUpdates") and seen["json"]["offset"] == 5
    client.send(
        tgmod.Message(text="hi", buttons=[[tgmod.Button("👍", "fb:u:1")]]),
    )
    assert seen["json"]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "fb:u:1"
    assert seen["json"]["chat_id"] == "99" and seen["json"]["parse_mode"] == "HTML"

    monkeypatch.setattr(tgmod.http, "post_json", lambda *a, **k: {"ok": False, "description": "no"})
    import pytest

    with pytest.raises(tgmod.http.HTTPError):
        client.get_updates()


def test_chat_not_found_is_explained_and_not_retried_as_plain_text(monkeypatch):
    body = '{"ok": false, "error_code": 400, "description": "Bad Request: chat not found"}'
    calls = []

    def fake_post(url, **kw):
        calls.append(kw)
        raise tgmod.http.HTTPError("POST api.telegram.org failed: HTTP 400", 400, body)

    monkeypatch.setattr(tgmod.http, "post_json", fake_post)
    import pytest

    with pytest.raises(tgmod.http.HTTPError) as e:
        tgmod.TelegramClient("t", "1").send(tgmod.Message(text="hi"))
    assert "chat not found" in str(e.value) and "Start" in str(e.value)
    assert len(calls) == 1  # not pointlessly resent as plain text
