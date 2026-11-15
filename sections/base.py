"""The plugin contract: subclass SectionPlugin, implement fetch() -> Section, add @register."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from core.context import Context
from core.models import Section

REGISTRY: dict[str, type[SectionPlugin]] = {}


class SectionPlugin(ABC):
    name: ClassVar[str]  # key used under `sections:` in config.yaml
    title: ClassVar[str]
    emoji: ClassVar[str]

    def __init__(self, ctx: Context, options: dict[str, Any]) -> None:
        self.ctx = ctx
        self.options = options

    @abstractmethod
    def fetch(self) -> Section:
        """Return the section. Raise on failure - the runner isolates it from other sections."""

    def make(self, **kwargs: Any) -> Section:
        kwargs.setdefault("title", self.title)
        return Section(name=self.name, emoji=self.emoji, **kwargs)


def register(cls: type[SectionPlugin]) -> type[SectionPlugin]:
    REGISTRY[cls.name] = cls
    return cls
