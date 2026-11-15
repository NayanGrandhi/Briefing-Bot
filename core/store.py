"""SQLite state: key/value state, LLM cache, sent items and feedback.

The DB uses the default rollback journal (no -wal/-shm files) so a single file is committed.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

SCHEMA = """
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS items(
    id TEXT PRIMARY KEY, url TEXT, title TEXT, source TEXT, topics TEXT, sent_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS feedback(
    item_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, ts REAL NOT NULL,
    PRIMARY KEY(item_id, kind));
"""

VOTE_WEIGHT = {"up": 1.0, "down": -1.0}
SAVE_WEIGHT = 1.5


@dataclass
class Preferences:
    source: dict[str, float] = field(default_factory=dict)
    topic: dict[str, float] = field(default_factory=dict)


F = TypeVar("F", bound=Callable)


def _locked(fn: F) -> F:
    """Serialise access: sections run in worker threads and share one connection."""

    @wraps(fn)
    def wrapper(self: Store, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)

    return wrapper  # type: ignore[return-value]


class Store:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- key/value state -------------------------------------------------
    @_locked
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    @_locked
    def set_state(self, key: str, value: str | None) -> None:
        with self.conn:
            if value is None:
                self.conn.execute("DELETE FROM state WHERE key=?", (key,))
            else:
                self.conn.execute(
                    "INSERT INTO state(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

    @_locked
    def get_json(self, key: str, default: Any) -> Any:
        raw = self.get_state(key)
        return json.loads(raw) if raw else default

    @_locked
    def set_json(self, key: str, value: Any) -> None:
        self.set_state(key, json.dumps(value, ensure_ascii=False))

    @_locked
    def incr(self, key: str) -> int:
        n = int(self.get_state(key, "0") or 0) + 1
        self.set_state(key, str(n))
        return n

    # ---- LLM cache -------------------------------------------------------
    @_locked
    def cache_get(self, key: str, ttl_seconds: float) -> str | None:
        row = self.conn.execute("SELECT value, created FROM cache WHERE key=?", (key,)).fetchone()
        if row and time.time() - row["created"] <= ttl_seconds:
            return row["value"]
        return None

    @_locked
    def cache_set(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO cache(key, value, created) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, created=excluded.created",
                (key, value, time.time()),
            )

    # ---- sent items & feedback -------------------------------------------
    @_locked
    def remember_item(self, item_id: str, meta: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO items(id, url, title, source, topics, sent_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET sent_at=excluded.sent_at",
                (
                    item_id,
                    meta.get("url", ""),
                    meta.get("title", ""),
                    meta.get("source", ""),
                    json.dumps(meta.get("topics", [])),
                    time.time(),
                ),
            )

    @_locked
    def was_sent(self, item_id: str, within_days: float = 7) -> bool:
        row = self.conn.execute("SELECT sent_at FROM items WHERE id=?", (item_id,)).fetchone()
        return bool(row and time.time() - row["sent_at"] <= within_days * 86400)

    @_locked
    def get_item(self, item_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    @_locked
    def feedback_state(self, item_id: str) -> tuple[str | None, bool]:
        rows = self.conn.execute(
            "SELECT kind, value FROM feedback WHERE item_id=?", (item_id,)
        ).fetchall()
        by_kind = {r["kind"]: r["value"] for r in rows}
        return by_kind.get("vote"), "save" in by_kind

    @_locked
    def set_vote(self, item_id: str, value: str) -> str | None:
        """Record up/down. Pressing the same vote again clears it. Returns the new vote."""
        current, _ = self.feedback_state(item_id)
        with self.conn:
            if current == value:
                self.conn.execute(
                    "DELETE FROM feedback WHERE item_id=? AND kind='vote'", (item_id,)
                )
                return None
            self.conn.execute(
                "INSERT INTO feedback(item_id, kind, value, ts) VALUES(?, 'vote', ?, ?) "
                "ON CONFLICT(item_id, kind) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (item_id, value, time.time()),
            )
        return value

    @_locked
    def toggle_save(self, item_id: str) -> bool:
        _, saved = self.feedback_state(item_id)
        with self.conn:
            if saved:
                self.conn.execute(
                    "DELETE FROM feedback WHERE item_id=? AND kind='save'", (item_id,)
                )
                return False
            self.conn.execute(
                "INSERT INTO feedback(item_id, kind, value, ts) VALUES(?, 'save', '1', ?)",
                (item_id, time.time()),
            )
        return True

    @_locked
    def saved_items(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT i.* FROM items i JOIN feedback f ON f.item_id=i.id AND f.kind='save' "
            "ORDER BY f.ts DESC LIMIT ?",
            (limit,),
        ).fetchall()

    @_locked
    def preferences(self, half_life_days: float = 60.0) -> Preferences:
        """Net, time-decayed feedback per source and topic, squashed into [-1, 1]."""
        rows = self.conn.execute(
            "SELECT f.kind, f.value, f.ts, i.source, i.topics FROM feedback f "
            "JOIN items i ON i.id=f.item_id"
        ).fetchall()
        src: dict[str, float] = {}
        top: dict[str, float] = {}
        now = time.time()
        for r in rows:
            weight = SAVE_WEIGHT if r["kind"] == "save" else VOTE_WEIGHT.get(r["value"], 0.0)
            weight *= 0.5 ** ((now - r["ts"]) / 86400 / half_life_days)
            if r["source"]:
                src[r["source"]] = src.get(r["source"], 0.0) + weight
            for t in json.loads(r["topics"] or "[]"):
                top[t] = top.get(t, 0.0) + weight
        return Preferences(
            source={k: math.tanh(v / 3) for k, v in src.items()},
            topic={k: math.tanh(v / 3) for k, v in top.items()},
        )

    # ---- housekeeping ----------------------------------------------------
    @_locked
    def prune(self, cache_days: float = 7, item_days: float = 60) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute("DELETE FROM cache WHERE created < ?", (now - cache_days * 86400,))
            self.conn.execute(
                "DELETE FROM items WHERE sent_at < ? AND id NOT IN (SELECT item_id FROM feedback)",
                (now - item_days * 86400,),
            )
