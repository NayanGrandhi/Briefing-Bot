"""Turn Sections into Telegram messages (HTML), compact style."""

from __future__ import annotations

import html
from datetime import datetime

from core.models import Button, Item, Message, Section
from providers.telegram import strip_html


def feedback_buttons(
    item_id: str, vote: str | None = None, saved: bool = False
) -> list[list[Button]]:
    return [
        [
            Button("👍 ✓" if vote == "up" else "👍", f"fb:u:{item_id}"),
            Button("👎 ✓" if vote == "down" else "👎", f"fb:d:{item_id}"),
            Button("🔖 Saved" if saved else "🔖 Save", f"fb:s:{item_id}"),
        ]
    ]


def _header(section: Section) -> str:
    return f"{section.emoji} <b>{html.escape(section.title)}</b>"


def _section_block(section: Section) -> list[str]:
    if not section.ok:
        err = html.escape((section.error or "unknown error")[:90])
        return [f"⚠️ <b>{html.escape(section.title)}</b> unavailable", f"<i>{err}</i>"]
    return [_header(section), *section.lines]


def render_section(section: Section) -> list[Message]:
    """One section as standalone message(s), without the daily greeting (used by /score)."""
    msgs = [Message(text="\n".join(_section_block(section)))]
    if section.ok:
        msgs.extend(_item_message(i) for i in section.items)
    return msgs


def greeting(now: datetime) -> str:
    hour = now.hour
    word = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    return f"☀️ <b>{word} — {now:%a %d %b}</b>"


def render(sections: list[Section], intro: str, now: datetime) -> list[Message]:
    messages: list[Message] = []
    batch: list[str] = [greeting(now)]
    if intro:
        batch.append(f"<i>{html.escape(intro)}</i>")

    def flush() -> None:
        nonlocal batch
        if batch:
            messages.append(Message(text="\n".join(batch)))
        batch = []

    for section in sections:
        if section.ok and not section.lines and not section.items:
            continue
        if batch:
            batch.append("")  # blank line between blocks
        batch.extend(_section_block(section))
        if section.ok and section.items:
            flush()
            for item in section.items:
                messages.append(_item_message(item))
    flush()
    # drop trailing empty-line artefacts
    for m in messages:
        m.text = m.text.strip("\n")
    return messages


def _item_message(item: Item) -> Message:
    return Message(text=item.text, buttons=feedback_buttons(item.id) if item.feedback else [])


def to_plain(messages: list[Message]) -> str:
    """Terminal rendering for --dry-run."""
    out: list[str] = []
    for i, m in enumerate(messages, 1):
        out.append(f"┌─ message {i}/{len(messages)} " + "─" * 40)
        out.extend(f"│ {line}" for line in strip_html(m.text).split("\n"))
        if m.buttons:
            out.append("│ [" + " | ".join(b.label for row in m.buttons for b in row) + "]")
        out.append("└" + "─" * 55)
    return "\n".join(out)
