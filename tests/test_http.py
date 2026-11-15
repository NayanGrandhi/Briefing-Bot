import pytest
import requests

from core import http


def _resp(status, headers=None):
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers or {})
    r._content = b'{"ok": true}'
    return r


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    delays = []
    monkeypatch.setattr(http, "_sleep", delays.append)
    return delays


def test_retries_then_succeeds_with_exponential_backoff(monkeypatch, no_sleep):
    seq = iter([_resp(503), _resp(502), _resp(200)])
    monkeypatch.setattr(requests, "request", lambda *a, **k: next(seq))
    assert http.get_json("https://x.test/a") == {"ok": True}
    assert len(no_sleep) == 2
    assert no_sleep[1] > no_sleep[0]  # backoff grows


def test_timeout_is_passed_and_connection_errors_retry(monkeypatch):
    seen = []

    def fake(*a, **k):
        seen.append(k["timeout"])
        raise requests.ConnectionError("boom https://x.test/secret")

    monkeypatch.setattr(requests, "request", fake)
    with pytest.raises(http.HTTPError) as e:
        http.get_json("https://x.test/secret", timeout=3, retries=2)
    assert seen == [3, 3, 3]
    assert "secret" not in str(e.value)  # URL path never leaks into errors


def test_client_errors_are_not_retried(monkeypatch, no_sleep):
    calls = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: calls.append(1) or _resp(404))
    with pytest.raises(http.HTTPError) as e:
        http.get_text("https://x.test/a")
    assert e.value.status == 404 and len(calls) == 1 and not no_sleep


def test_retry_after_header_is_honoured_and_capped(monkeypatch, no_sleep):
    seq = iter([_resp(429, {"Retry-After": "999"}), _resp(200)])
    monkeypatch.setattr(requests, "request", lambda *a, **k: next(seq))
    http.get_json("https://x.test/a")
    assert no_sleep[0] <= http.MAX_RETRY_AFTER * 1.2


def test_secrets_are_redacted(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEFGHIJKLMNOP")
    from core.secrets import redact

    assert "ABCDEF" not in redact("https://api.telegram.org/bot123456:ABCDEFGHIJKLMNOP/send")
