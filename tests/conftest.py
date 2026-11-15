from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.ai import AIService
from core.config import Config, Feed, Location, Topic
from core.context import Context
from core.models import Message
from core.store import Store
from providers.llm.base import LLMError, LLMProvider

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 15, 7, 0, tzinfo=IST)


class FakeLLM(LLMProvider):
    """Scripted LLM: returns queued responses (or raises queued exceptions)."""

    name = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt, *, max_tokens=512, json_mode=False):
        self.calls.append(prompt)
        if not self.responses:
            raise LLMError("no more scripted responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeTG:
    """Records everything the bot would send to Telegram."""

    chat_id = "42"

    def __init__(self):
        self.sent: list[Message] = []
        self.answers: list[tuple[str, str]] = []
        self.edits: list[tuple[int, list]] = []
        self.updates: list[dict] = []

    def send(self, msg):
        self.sent.append(msg)

    def get_updates(self, offset=None):
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]

    def answer_callback(self, cid, text=""):
        self.answers.append((cid, text))

    def edit_buttons(self, mid, buttons):
        self.edits.append((mid, buttons))

    def alert(self, text):
        self.sent.append(Message(text=text))
        return True

    @property
    def texts(self):
        return "\n".join(m.text for m in self.sent)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def config():
    return Config(
        location=Location("Hyderabad", 17.385, 78.4867),
        topics=[
            Topic("Tech & AI", ["ai", "openai", "nvidia", "gpu"]),
            Topic("Football", ["football", "arsenal", "derby"]),
        ],
        feeds=[Feed("Feed A", "https://a.test/rss"), Feed("Feed B", "https://b.test/rss")],
        sections={
            "weather": {"enabled": True},
            "news": {"enabled": True},
            "tasks": {"enabled": True},
        },
    )


@pytest.fixture
def make_ctx(store, config):
    def _make(llm=None):
        ai = AIService(llm, store, daily_budget=30, today=NOW.date())
        return Context(config, store, ai, NOW)

    return _make


@pytest.fixture
def tg():
    return FakeTG()
