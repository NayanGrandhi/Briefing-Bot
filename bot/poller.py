"""Polling mode: drain pending Telegram updates once, then exit (no 24/7 server needed)."""

from __future__ import annotations

import logging
from datetime import timedelta

from bot.commands import handle_callback, handle_text
from core.context import Context
from core.runner import run_briefing
from providers.telegram import TelegramClient

log = logging.getLogger("poller")


def check_health(ctx: Context, tg: TelegramClient) -> bool:
    """Alert once per day if the scheduled briefing never went out (e.g. Actions was down)."""
    st, today = ctx.store, ctx.today.isoformat()
    last = st.get_state("last_sent_date")
    if last is None or last == today or st.get_state("skipped_date") == today:
        return False  # never sent before (fresh install), already sent, or skipped on purpose
    if st.get_state("skip_date") == today or st.get_state("health_alerted") == today:
        return False
    hh, mm = map(int, ctx.config.send_time.split(":"))
    due = ctx.now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(minutes=90)
    if ctx.now < due:
        return False
    st.set_state("health_alerted", today)
    return tg.alert(f"No briefing was sent today (last one: {last}). Send one now with /now.")


def poll(ctx: Context, tg: TelegramClient | None, dry_run: bool = False) -> int:
    """Process pending updates. Returns how many were handled."""
    if tg is None:
        log.info("poll dry-run: no Telegram client, nothing to do")
        return 0
    offset_raw = ctx.store.get_state("tg_offset")
    updates = tg.get_updates(int(offset_raw) if offset_raw else None)
    want_now = False
    handled = 0
    for upd in updates:
        offset = upd["update_id"] + 1
        try:
            if "callback_query" in upd:
                cb = upd["callback_query"]
                if str((cb.get("message") or {}).get("chat", {}).get("id")) == tg.chat_id:
                    handle_callback(ctx, tg, cb)
                    handled += 1
            elif "message" in upd:
                msg = upd["message"]
                text = msg.get("text") or ""
                if str(msg.get("chat", {}).get("id")) != tg.chat_id:
                    log.warning("ignored message from unknown chat")
                elif text.startswith("/"):
                    want_now = handle_text(ctx, tg, text) or want_now
                    handled += 1
        except Exception:  # noqa: BLE001 - one bad update must not block the queue
            log.exception("failed to handle update", extra={"update_id": upd.get("update_id")})
        ctx.store.set_state("tg_offset", str(offset))  # persist progress per update
    if want_now:
        run_briefing(ctx, tg, force=True)
    if not dry_run:
        check_health(ctx, tg)
    log.info("poll done", extra={"updates": len(updates), "handled": handled})
    return handled
