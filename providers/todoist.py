"""Todoist API v1: tasks due today or overdue via the filter endpoint (free tier works)."""

from __future__ import annotations

from datetime import date
from typing import Any

from core import http

FILTER_URL = "https://api.todoist.com/api/v1/tasks/filter"
MAX_PAGES = 5


def fetch_due(token: str) -> list[tuple[str, date | None]]:
    """Returns [(task text, due date)] for 'overdue | today'."""
    out: list[tuple[str, date | None]] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"query": "overdue | today", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = http.get_json(
            FILTER_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            retries=2,
        )
        for t in data.get("results", []):
            if t.get("checked") or t.get("completed_at"):
                continue
            due_raw = ((t.get("due") or {}).get("date") or "")[:10]
            try:
                due = date.fromisoformat(due_raw) if due_raw else None
            except ValueError:
                due = None
            out.append((str(t.get("content", "")).strip(), due))
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return out
