"""Secret redaction so tokens never reach logs, alerts or error messages."""

from __future__ import annotations

import os
import re

SECRET_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "CALENDAR_ICAL_URL",
    "FOOTBALL_DATA_API_KEY",
    "TODOIST_API_TOKEN",
)
_TELEGRAM_TOKEN_RE = re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{10,}")


def redact(text: str) -> str:
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            text = text.replace(value, "***")
    return _TELEGRAM_TOKEN_RE.sub("bot***", text)
