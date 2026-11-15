"""HTTP helper: timeout + retries with exponential backoff on every network call."""

from __future__ import annotations

import logging
import random
import time
from typing import Any
from urllib.parse import urlparse

import requests

from core.secrets import redact

log = logging.getLogger("http")

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_TIMEOUT = 10.0
USER_AGENT = "briefing-bot/1.0 (personal daily briefing)"
MAX_RETRY_AFTER = 30.0


class HTTPError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(redact(message))
        self.status = status
        self.body = redact(body[:500])


def _sleep(seconds: float) -> None:  # separate function so tests can patch it
    time.sleep(seconds)


def _retry_after(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    try:
        return min(float(raw), MAX_RETRY_AFTER) if raw else None
    except ValueError:
        return None


def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json: Any = None,
    data: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3,
    backoff: float = 1.0,
) -> requests.Response:
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    host = urlparse(url).hostname or "?"
    last_error, last_status, last_body = "unknown error", None, ""
    for attempt in range(retries + 1):
        retry_after = None
        try:
            resp = requests.request(
                method, url, params=params, headers=hdrs, json=json, data=data, timeout=timeout
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error, last_status = type(exc).__name__, None
        else:
            if resp.status_code < 400:
                return resp
            last_status, last_error = resp.status_code, f"HTTP {resp.status_code}"
            last_body = resp.text or ""
            if resp.status_code not in RETRY_STATUSES:
                break
            retry_after = _retry_after(resp)
        if attempt < retries:
            delay = retry_after if retry_after is not None else backoff * 2**attempt
            delay *= random.uniform(0.8, 1.2)
            log.warning(
                "retrying request",
                extra={"host": host, "attempt": attempt + 1, "reason": last_error, "delay": delay},
            )
            _sleep(delay)
    raise HTTPError(f"{method} {host} failed: {last_error}", last_status, last_body)


def _json(resp: requests.Response, host: str) -> Any:
    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPError(f"{host} returned invalid JSON") from exc


def get_json(url: str, **kwargs: Any) -> Any:
    return _json(request("GET", url, **kwargs), urlparse(url).hostname or "?")


def get_text(url: str, **kwargs: Any) -> str:
    resp = request("GET", url, **kwargs)
    return resp.text


def post_json(url: str, **kwargs: Any) -> Any:
    return _json(request("POST", url, **kwargs), urlparse(url).hostname or "?")
