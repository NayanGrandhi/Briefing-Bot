"""Minimal Telegram Bot API client."""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

from core import http
from core.models import Button, Message

log = logging.getLogger("telegram")
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram's hard limit is 4096


def split_text(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on line boundaries so HTML tags (which never span lines here) stay intact."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # pathological single line
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


HINTS = {
    "chat not found": "check TELEGRAM_CHAT_ID, and press Start on your bot in Telegram",
    "bot was blocked": "you blocked the bot; unblock it in Telegram",
    "bots can't send messages to bots": "TELEGRAM_CHAT_ID is a bot id; use yours from @userinfobot",
    "unauthorized": "TELEGRAM_BOT_TOKEN is wrong; copy it again from @BotFather",
    "not found": "TELEGRAM_BOT_TOKEN is wrong; copy it again from @BotFather",
}


def describe(exc: http.HTTPError) -> str:
    """Telegram's own error text plus a plain-English fix."""
    try:
        desc = json.loads(exc.body).get("description", "")
    except ValueError:
        desc = ""
    hint = next((h for k, h in HINTS.items() if k in desc.lower()), "")
    return " - ".join(x for x in (desc or "no details", hint) if x)


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def markup(buttons: list[list[Button]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": b.label, "callback_data": b.data} for b in row] for row in buttons
        ]
    }


class TelegramClient:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = str(chat_id)

    def _call(self, method: str, payload: dict[str, Any] | None = None, timeout: float = 15) -> Any:
        try:
            data = http.post_json(
                API.format(token=self.token, method=method), json=payload or {}, timeout=timeout
            )
        except http.HTTPError as exc:
            raise http.HTTPError(f"{exc} ({describe(exc)})", exc.status, exc.body) from exc
        if not data.get("ok"):
            raise http.HTTPError(f"Telegram {method} not ok: {data.get('description')}")
        return data["result"]

    def send(self, msg: Message) -> None:
        """Send a message (split if long). Falls back to plain text if HTML is rejected."""
        chunks = split_text(msg.text)
        for i, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if msg.buttons and i == len(chunks) - 1:
                payload["reply_markup"] = markup(msg.buttons)
            try:
                self._call("sendMessage", payload)
            except http.HTTPError as exc:
                if exc.status != 400 or "parse entities" not in exc.body.lower():
                    raise
                log.warning("html rejected, resending as plain text")
                payload.pop("parse_mode")
                payload["text"] = strip_html(chunk)
                self._call("sendMessage", payload)

    def send_text(self, text: str) -> None:
        self.send(Message(text=html.escape(text)))

    def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def edit_buttons(self, message_id: int, buttons: list[list[Button]]) -> None:
        self._call(
            "editMessageReplyMarkup",
            {"chat_id": self.chat_id, "message_id": message_id, "reply_markup": markup(buttons)},
        )

    def alert(self, text: str) -> bool:
        """Best-effort error alert. Never raises."""
        try:
            self.send(Message(text=f"🚨 <b>Briefing bot</b>\n{html.escape(text[:1500])}"))
            return True
        except Exception:  # noqa: BLE001 - an alert path must not raise
            log.exception("could not deliver error alert")
            return False
