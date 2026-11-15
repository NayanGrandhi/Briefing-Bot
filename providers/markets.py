"""Free market data: Yahoo Finance chart/screener (unofficial, keyless) and CoinGecko."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from core import http

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SCREENER = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"


@dataclass
class Quote:
    name: str
    change_pct: float
    as_of: date | None = None  # date of the latest trading session (None for crypto)


def yahoo_daily_change(symbol: str, name: str) -> Quote:
    """Daily % change = latest price vs the previous session's close."""
    data = http.get_json(
        YAHOO_CHART.format(symbol=quote(symbol, safe="")),
        params={"range": "5d", "interval": "1d"},
        timeout=10,
        retries=2,
    )
    result = data["chart"]["result"][0]
    closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    stamps = result.get("timestamp") or []
    if len(closes) < 2:
        raise ValueError(f"not enough history for {symbol}")
    last = result["meta"].get("regularMarketPrice") or closes[-1]
    prev = closes[-2]
    tz = ZoneInfo(result["meta"].get("exchangeTimezoneName") or "UTC")
    as_of = datetime.fromtimestamp(stamps[-1], UTC).astimezone(tz).date() if stamps else None
    return Quote(name, (last / prev - 1) * 100, as_of)


def coingecko_changes(coins: list[tuple[str, str]]) -> list[Quote]:
    """24h % change for [(coingecko_id, display_name)]."""
    data = http.get_json(
        COINGECKO,
        params={
            "ids": ",".join(c for c, _ in coins),
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        },
        timeout=10,
        retries=2,
    )
    out = []
    for coin_id, name in coins:
        change = (data.get(coin_id) or {}).get("usd_24h_change")
        if change is not None:
            out.append(Quote(name, float(change)))
    return out


def yahoo_movers(kind: str, min_market_cap: float, limit: int) -> list[Quote]:
    """kind: 'day_gainers' | 'day_losers'. Filters out penny stocks / micro caps."""
    data = http.get_json(
        YAHOO_SCREENER,
        params={"formatted": "false", "scrIds": kind, "count": 25},
        timeout=10,
        retries=1,
    )
    rows = data["finance"]["result"][0]["quotes"]
    out = [
        Quote(
            f"{r.get('shortName') or r['symbol']} ({r['symbol']})",
            float(r["regularMarketChangePercent"]),
        )
        for r in rows
        if (r.get("marketCap") or 0) >= min_market_cap and (r.get("regularMarketPrice") or 0) >= 5
    ]
    return out[:limit]
