"""Everything a section plugin may need, passed in explicitly (no globals)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from core.ai import AIService
from core.config import Config
from core.store import Store


@dataclass
class Context:
    config: Config
    store: Store
    ai: AIService
    now: datetime  # timezone-aware, in config.tz

    @property
    def today(self) -> date:
        return self.now.date()
