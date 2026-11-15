"""Markets: daily change only, for a configurable watchlist, plus big movers."""

from __future__ import annotations

import html
import logging
from datetime import date

from core.models import Section
from providers import markets
from providers.markets import Quote
from sections.base import SectionPlugin, register

log = logging.getLogger("markets")


def fmt(q: Quote, today: date | None = None, flag_pct: float = 5.0) -> str:
    dot = "🟢" if q.change_pct >= 0 else "🔴"
    sign = "+" if q.change_pct >= 0 else "−"
    fire = " 🔥" if abs(q.change_pct) >= flag_pct else ""
    stale = ""
    if today and q.as_of and (today - q.as_of).days >= 2:
        stale = f" <i>({q.as_of:%a} close)</i>"
    return f"{dot} {html.escape(q.name)} {sign}{abs(q.change_pct):.1f}%{fire}{stale}"


@register
class MarketsSection(SectionPlugin):
    name = "markets"
    title = "Markets"
    emoji = "📈"

    def fetch(self) -> Section:
        o = self.options
        flag = float(o.get("flag_threshold_pct", 5))
        today = self.ctx.today
        rows: list[str] = []
        digest_bits: list[str] = []

        # Each source fails independently: a Yahoo outage must not hide crypto, and vice versa.
        stock_quotes: list[Quote] = []
        for s in o.get("symbols", []):
            try:
                stock_quotes.append(
                    markets.yahoo_daily_change(s["symbol"], s.get("name", s["symbol"]))
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("quote failed", extra={"symbol": s.get("symbol"), "err": str(exc)})
        if stock_quotes:
            rows.append(" · ".join(fmt(q, today, flag) for q in stock_quotes))

        coins = [(c["id"], c.get("name", c["id"])) for c in o.get("crypto", [])]
        crypto_quotes: list[Quote] = []
        if coins:
            try:
                crypto_quotes = markets.coingecko_changes(coins)
            except Exception as exc:  # noqa: BLE001
                log.warning("crypto failed", extra={"err": str(exc)})
        if crypto_quotes:
            rows.append(" · ".join(fmt(q, None, flag) for q in crypto_quotes))

        movers_cfg = o.get("movers") or {}
        if movers_cfg.get("enabled"):
            n = int(movers_cfg.get("max_each", 2))
            cap = float(movers_cfg.get("min_market_cap", 1e10))
            for label, kind in (("🚀 Surging", "day_gainers"), ("📉 Sliding", "day_losers")):
                try:
                    qs = markets.yahoo_movers(kind, cap, n)
                except Exception as exc:  # noqa: BLE001
                    log.warning("movers failed", extra={"kind": kind, "err": str(exc)})
                    continue
                if qs:
                    rows.append(
                        f"{label}: "
                        + " · ".join(fmt(q, None, float("inf")).split(" ", 1)[1] for q in qs)
                    )

        if not rows:
            raise RuntimeError("no market data available from any source")

        allq = [*stock_quotes, *crypto_quotes]
        if allq:
            big = max(allq, key=lambda q: abs(q.change_pct))
            digest_bits.append(f"biggest watchlist move: {big.name} {big.change_pct:+.1f}%")
        return self.make(lines=rows, digest="; ".join(digest_bits))
