"""Shared dataclasses passed between sections, the renderer and the Telegram client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Item:
    """One standalone message inside a section (e.g. a news story with feedback buttons)."""

    id: str
    text: str  # Telegram-HTML
    meta: dict[str, Any] = field(default_factory=dict)
    feedback: bool = False


@dataclass(slots=True)
class Section:
    name: str
    title: str
    emoji: str
    lines: list[str] = field(default_factory=list)  # Telegram-HTML lines
    items: list[Item] = field(default_factory=list)
    digest: str = ""  # plain-text fact fed to the "day at a glance" intro
    ok: bool = True
    error: str | None = None

    @classmethod
    def failed(cls, name: str, title: str, emoji: str, error: str) -> Section:
        return cls(name=name, title=title, emoji=emoji, ok=False, error=error)


@dataclass(slots=True)
class Button:
    label: str
    data: str


@dataclass(slots=True)
class Message:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)


@dataclass(slots=True)
class Briefing:
    sections: list[Section]
    intro: str
    messages: list[Message]

    @property
    def failed(self) -> list[str]:
        return [s.name for s in self.sections if not s.ok]
