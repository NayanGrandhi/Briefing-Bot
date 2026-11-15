"""Tasks: due-today and overdue items from a plain tasks.md checklist."""

from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.models import Section
from providers import todoist
from sections.base import SectionPlugin, register

log = logging.getLogger("tasks")
_LINE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*\S)\s*$")
_DUE = re.compile(r"(?:📅\s*|\bdue:|@due\()(\d{4}-\d{2}-\d{2})\)?")


@dataclass
class Task:
    text: str
    due: date | None
    done: bool


def parse_tasks(markdown: str) -> list[Task]:
    tasks: list[Task] = []
    for line in markdown.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        body = m.group(2)
        due = None
        dm = _DUE.search(body)
        if dm:
            try:
                due = date.fromisoformat(dm.group(1))
            except ValueError:
                due = None
            body = _DUE.sub("", body).strip()
        tasks.append(Task(body, due, m.group(1).lower() == "x"))
    return tasks


def _cap(rows: list[str], limit: int = 6) -> list[str]:
    return rows[:limit] + ([f"<i>+{len(rows) - limit} more</i>"] if len(rows) > limit else [])


@register
class TasksSection(SectionPlugin):
    name = "tasks"
    title = "Tasks"
    emoji = "✅"

    def fetch(self) -> Section:
        path = Path(self.options.get("file") or self.ctx.config.tasks_file)
        token = os.environ.get("TODOIST_API_TOKEN") if self.options.get("todoist", True) else None
        tasks: list[Task] = []
        if path.is_file():
            tasks += parse_tasks(path.read_text(encoding="utf-8"))
        elif not token:
            raise FileNotFoundError(f"{path} not found")
        if token:
            try:
                tasks += [Task(text, due, False) for text, due in todoist.fetch_due(token)]
            except Exception as exc:  # noqa: BLE001 - keep tasks.md results if Todoist is down
                log.warning("todoist failed", extra={"err": str(exc)})
                if not path.is_file():
                    raise
        today = self.ctx.today
        seen: set[tuple[str, date | None]] = set()
        open_tasks = []
        for t in tasks:  # same task in both places counts once
            key = (t.text.lower(), t.due)
            if not t.done and key not in seen:
                seen.add(key)
                open_tasks.append(t)
        overdue = sorted((t for t in open_tasks if t.due and t.due < today), key=lambda t: t.due)  # type: ignore[arg-type,return-value]
        due_today = [t for t in open_tasks if t.due == today]
        if not overdue and not due_today:
            return self.make(lines=["Nothing due today 🎉"], digest="no tasks due")

        lines: list[str] = []
        if overdue:
            rows = [
                f"• {html.escape(t.text)} <i>({(today - t.due).days}d overdue)</i>"  # type: ignore[operator]
                for t in overdue
            ]
            lines.append(f"🔴 <b>Overdue ({len(overdue)})</b>")
            lines.extend(_cap(rows))
        if due_today:
            lines.append(f"🟡 <b>Due today ({len(due_today)})</b>")
            lines.extend(_cap([f"• {html.escape(t.text)}" for t in due_today]))
        parts = []
        if overdue:
            parts.append(f"{len(overdue)} overdue")
        if due_today:
            parts.append(f"{len(due_today)} due today")
        return self.make(lines=lines, digest="tasks: " + ", ".join(parts))
