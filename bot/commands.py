"""Telegram command + button handlers. Pure functions over (ctx, tg, update) for easy testing."""

from __future__ import annotations

import html
import logging
from typing import Any

from core import topics as topics_mod
from core.context import Context
from core.models import Message
from core.render import feedback_buttons, render_section
from core.runner import fetch_sections, tomorrow
from providers.telegram import TelegramClient

log = logging.getLogger("commands")

HELP = (
    "<b>Commands</b>\n"
    "/now — send a briefing right now\n"
    "/skip — skip tomorrow's briefing (<code>/skip cancel</code> to undo)\n"
    "/topics — list topics\n"
    "<code>/topics add Cricket: cricket, ipl, bcci</code>\n"
    "<code>/topics remove Cricket</code> · <code>/topics reset</code>\n"
    "/score — Real Madrid: last result, next match, live status\n"
    "/saved — your saved stories\n"
    "/help — this message\n\n"
    "<i>Replies can take up to ~30 min: I check Telegram on a schedule, not 24/7.</i>"
)


def _reply(tg: TelegramClient, text: str) -> None:
    tg.send(Message(text=text))


def handle_callback(ctx: Context, tg: TelegramClient, cb: dict[str, Any]) -> None:
    data = cb.get("data", "")
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "fb" or parts[1] not in {"u", "d", "s"}:
        tg.answer_callback(cb["id"])
        return
    _, action, item_id = parts
    store = ctx.store
    if store.get_item(item_id) is None:
        tg.answer_callback(cb["id"], "That story is too old to rate.")
        return
    if action == "s":
        saved = store.toggle_save(item_id)
        note = "Saved 🔖" if saved else "Removed from saved"
    else:
        vote = store.set_vote(item_id, "up" if action == "u" else "down")
        note = {"up": "Noted — more like this 👍", "down": "Noted — less like this 👎"}.get(
            vote or "", "Vote cleared"
        )
    tg.answer_callback(cb["id"], note)
    msg = cb.get("message") or {}
    if msg.get("message_id"):
        vote_now, saved_now = store.feedback_state(item_id)
        try:
            tg.edit_buttons(msg["message_id"], feedback_buttons(item_id, vote_now, saved_now))
        except Exception:  # noqa: BLE001 - cosmetic only; the feedback is already stored
            log.warning("could not update buttons")


def handle_text(ctx: Context, tg: TelegramClient, text: str) -> bool:
    """Handle a command. Returns True if a briefing should be sent now (/now)."""
    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()

    if cmd == "/now":
        _reply(tg, "On it — building your briefing…")
        return True
    if cmd == "/skip":
        if arg.lower() == "cancel":
            ctx.store.set_state("skip_date", None)
            _reply(tg, "Skip cancelled. Tomorrow's briefing will be sent.")
        else:
            day = tomorrow(ctx.now)
            ctx.store.set_state("skip_date", day)
            _reply(
                tg, f"Okay — no briefing on {html.escape(day)}. <code>/skip cancel</code> to undo."
            )
        return False
    if cmd == "/topics":
        _topics(ctx, tg, arg)
        return False
    if cmd == "/score":
        sections = fetch_sections(ctx, only="football")
        if not sections:
            _reply(tg, "The football section is switched off in config.yaml.")
        for msg in render_section(sections[0]) if sections else []:
            tg.send(msg)
        return False
    if cmd == "/saved":
        rows = ctx.store.saved_items()
        if not rows:
            _reply(tg, "No saved stories yet — tap 🔖 under a news item.")
        else:
            lines = [
                f'• <a href="{html.escape(r["url"], quote=True)}">{html.escape(r["title"])}</a>'
                for r in rows
            ]
            _reply(tg, "<b>🔖 Saved</b>\n" + "\n".join(lines))
        return False
    _reply(tg, HELP)
    return False


def _topics(ctx: Context, tg: TelegramClient, arg: str) -> None:
    verb, _, rest = arg.partition(" ")
    verb, rest = verb.lower(), rest.strip()
    if verb == "add" and rest:
        name, _, kws = rest.partition(":")
        keywords = [k.strip().lower() for k in kws.split(",") if k.strip()]
        topics_mod.add_topic(ctx.store, name.strip(), keywords)
        _reply(tg, f"Added topic <b>{html.escape(name.strip())}</b>.")
    elif verb == "remove" and rest:
        ok = topics_mod.remove_topic(ctx.config, ctx.store, rest)
        _reply(
            tg,
            f"Removed <b>{html.escape(rest)}</b>."
            if ok
            else f"No topic named {html.escape(rest)}.",
        )
    elif verb == "reset":
        topics_mod.reset_topics(ctx.store)
        _reply(tg, "Topics reset to config.yaml.")
    else:
        rows = [
            f"• <b>{html.escape(t.name)}</b>: {html.escape(', '.join(t.keywords[:8]))}"
            for t in topics_mod.effective_topics(ctx.config, ctx.store)
        ]
        _reply(
            tg,
            "<b>Topics</b>\n" + ("\n".join(rows) or "(none)") + "\n\n"
            "<code>/topics add Name: kw1, kw2</code> · <code>/topics remove Name</code>",
        )
