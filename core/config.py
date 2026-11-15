"""Typed loader/validator for config.yaml - the single place for personalisation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    pass


@dataclass
class Location:
    city: str = "Hyderabad"
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Topic:
    name: str
    keywords: list[str] = field(default_factory=list)
    weight: float = 1.0


@dataclass
class Feed:
    name: str
    url: str
    topic: str | None = None  # optional: every story from this feed counts toward this topic


@dataclass
class LLMConfig:
    provider: str = "gemini"
    model: str | None = None
    fallback_provider: str | None = None
    fallback_model: str | None = None
    daily_call_budget: int = 30
    cache_ttl_hours: int = 48


@dataclass
class Config:
    timezone: str = "Asia/Kolkata"
    send_time: str = "07:00"
    max_wait_minutes: int = 10
    tasks_file: str = "tasks.md"
    db_path: str = "data/state.db"
    location: Location = field(default_factory=Location)
    topics: list[Topic] = field(default_factory=list)
    feeds: list[Feed] = field(default_factory=list)
    sections: dict[str, dict[str, Any]] = field(default_factory=dict)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def section_enabled(self, name: str) -> bool:
        return bool(self.sections.get(name, {}).get("enabled", False))

    def section_options(self, name: str) -> dict[str, Any]:
        return self.sections.get(name, {})


def _topic(raw: Any) -> Topic:
    if isinstance(raw, str):
        return Topic(name=raw, keywords=[raw.lower()])
    if isinstance(raw, dict) and raw.get("name"):
        kws = [str(k).lower() for k in raw.get("keywords", [])] or [str(raw["name"]).lower()]
        return Topic(name=str(raw["name"]), keywords=kws, weight=float(raw.get("weight", 1.0)))
    raise ConfigError(f"Invalid topic entry: {raw!r}")


def _feed(raw: Any) -> Feed:
    if not isinstance(raw, dict) or not raw.get("url") or not raw.get("name"):
        raise ConfigError(f"Each feed needs 'name' and 'url': {raw!r}")
    url = str(raw["url"])
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"Feed URL must start with http(s)://: {url}")
    return Feed(name=str(raw["name"]), url=url, topic=raw.get("topic"))


def config_from_dict(raw: dict[str, Any] | None) -> Config:
    raw = raw or {}
    tz = str(raw.get("timezone", "Asia/Kolkata"))
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown timezone: {tz}") from exc

    send_time = str(raw.get("send_time", "07:00"))
    if not _TIME_RE.match(send_time):
        raise ConfigError(f'send_time must look like "07:00" (quote it in YAML), got {send_time!r}')

    loc_raw = raw.get("location") or {}
    location = Location(
        city=str(loc_raw.get("city", "Hyderabad")),
        latitude=loc_raw.get("latitude"),
        longitude=loc_raw.get("longitude"),
    )

    sections: dict[str, dict[str, Any]] = {}
    for name, opts in (raw.get("sections") or {}).items():
        if isinstance(opts, bool):
            opts = {"enabled": opts}
        opts = dict(opts or {})
        opts.setdefault("enabled", True)
        sections[str(name)] = opts

    llm_raw = raw.get("llm") or {}
    llm = LLMConfig(
        provider=str(llm_raw.get("provider", "gemini")),
        model=llm_raw.get("model"),
        fallback_provider=llm_raw.get("fallback_provider"),
        fallback_model=llm_raw.get("fallback_model"),
        daily_call_budget=int(llm_raw.get("daily_call_budget", 30)),
        cache_ttl_hours=int(llm_raw.get("cache_ttl_hours", 48)),
    )

    return Config(
        timezone=tz,
        send_time=send_time,
        max_wait_minutes=int(raw.get("max_wait_minutes", 10)),
        tasks_file=str(raw.get("tasks_file", "tasks.md")),
        db_path=str(raw.get("db_path", "data/state.db")),
        location=location,
        topics=[_topic(t) for t in raw.get("topics", [])],
        feeds=[_feed(f) for f in raw.get("feeds", [])],
        sections=sections,
        llm=llm,
    )


def load_config(path: str | Path = "config.yaml") -> Config:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml is not valid YAML: {exc}") from exc
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError("config.yaml must be a mapping at the top level")
    return config_from_dict(raw)
