"""Daily snippet: one short tech tip - LLM-written when available, curated list otherwise."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from core.models import Section
from sections.base import SectionPlugin, register

TIPS_FILE = Path(__file__).parent / "data" / "tips.json"
RECENT_KEY = "recent_tips"


def curated_tip(day_ordinal: int) -> str:
    tips: list[str] = json.loads(TIPS_FILE.read_text(encoding="utf-8"))
    return tips[day_ordinal % len(tips)]


@register
class SnippetSection(SectionPlugin):
    name = "snippet"
    title = "Tip of the day"
    emoji = "💡"

    def fetch(self) -> Section:
        topic = str(self.options.get("topic", "programming and developer tools"))
        store = self.ctx.store
        recent: list[str] = store.get_json(RECENT_KEY, [])
        tip = self.ctx.ai.tip(topic, recent)
        if tip:
            store.set_json(RECENT_KEY, [*recent, tip][-14:])
        else:
            tip = curated_tip(self.ctx.today.toordinal())
        return self.make(lines=[re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(tip))])
