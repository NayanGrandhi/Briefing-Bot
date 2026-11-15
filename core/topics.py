"""Topics = config.yaml topics, adjusted by /topics commands (stored in SQLite)."""

from __future__ import annotations

from core.config import Config, Topic
from core.store import Store


def effective_topics(cfg: Config, store: Store) -> list[Topic]:
    removed = {n.lower() for n in store.get_json("topics_removed", [])}
    topics = [t for t in cfg.topics if t.name.lower() not in removed]
    for extra in store.get_json("topics_added", []):
        if extra["name"].lower() not in {t.name.lower() for t in topics}:
            topics.append(Topic(name=extra["name"], keywords=extra["keywords"]))
    return topics


def add_topic(store: Store, name: str, keywords: list[str]) -> None:
    removed = [n for n in store.get_json("topics_removed", []) if n.lower() != name.lower()]
    store.set_json("topics_removed", removed)
    added = [t for t in store.get_json("topics_added", []) if t["name"].lower() != name.lower()]
    added.append({"name": name, "keywords": keywords or [name.lower()]})
    store.set_json("topics_added", added)


def remove_topic(cfg: Config, store: Store, name: str) -> bool:
    """Returns False if no topic with that name exists."""
    existing = {t.name.lower(): t.name for t in effective_topics(cfg, store)}
    if name.lower() not in existing:
        return False
    store.set_json(
        "topics_added",
        [t for t in store.get_json("topics_added", []) if t["name"].lower() != name.lower()],
    )
    if name.lower() in {t.name.lower() for t in cfg.topics}:
        removed = store.get_json("topics_removed", [])
        store.set_json("topics_removed", [*removed, existing[name.lower()]])
    return True


def reset_topics(store: Store) -> None:
    store.set_json("topics_added", [])
    store.set_json("topics_removed", [])
