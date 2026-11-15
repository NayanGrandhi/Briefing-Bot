"""Build and deliver the briefing. Design rule: a briefing is ALWAYS sent."""

from __future__ import annotations

import html
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta

from core.context import Context
from core.models import Briefing, Message, Section
from core.render import render, to_plain
from core.secrets import redact
from providers.telegram import TelegramClient
from sections import SectionPlugin, load_plugins

log = logging.getLogger("runner")

SECTION_TIMEOUT = 90.0


def _run_one(plugin_cls: type[SectionPlugin], ctx: Context, options: dict) -> Section:
    started = time.monotonic()
    try:
        section = plugin_cls(ctx, options).fetch()
        log.info(
            "section ok",
            extra={"section": plugin_cls.name, "secs": round(time.monotonic() - started, 2)},
        )
        return section
    except Exception as exc:  # noqa: BLE001 - isolation boundary: each section fails on its own
        log.exception("section failed", extra={"section": plugin_cls.name})
        return Section.failed(plugin_cls.name, plugin_cls.title, plugin_cls.emoji, redact(str(exc)))


def fetch_sections(ctx: Context, only: str | None = None) -> list[Section]:
    plugins = load_plugins()
    names = []
    for name in ctx.config.sections:
        if not ctx.config.section_enabled(name) or (only and name != only):
            continue
        if name not in plugins:
            log.warning("unknown section in config", extra={"section": name})
            continue
        names.append(name)
    if not names:
        return []
    results: list[Section] = []
    pool = ThreadPoolExecutor(max_workers=len(names))
    futures = {
        n: pool.submit(_run_one, plugins[n], ctx, ctx.config.section_options(n)) for n in names
    }
    deadline = time.monotonic() + SECTION_TIMEOUT
    for n in names:  # keep config order
        cls = plugins[n]
        try:
            results.append(futures[n].result(timeout=max(0.1, deadline - time.monotonic())))
        except FutureTimeout:
            log.error("section timed out", extra={"section": n})
            results.append(Section.failed(n, cls.title, cls.emoji, "timed out"))
    pool.shutdown(wait=False, cancel_futures=True)
    return results


def fallback_intro(sections: list[Section]) -> str:
    facts = [s.digest for s in sections if s.ok and s.digest and s.name != "news"]
    return ("Today: " + "; ".join(facts) + ".") if facts else ""


def build_briefing(ctx: Context, only: str | None = None) -> Briefing:
    sections = fetch_sections(ctx, only)
    facts = [s.digest for s in sections if s.ok and s.digest]
    intro = ""
    if not only:
        try:
            intro = ctx.ai.day_intro(facts) or ""
        except Exception:  # noqa: BLE001 - the intro is a nicety, never a blocker
            log.exception("intro generation failed")
        intro = intro or fallback_intro(sections)
    return Briefing(sections, intro, render(sections, intro, ctx.now))


def minimal_briefing(ctx: Context, error: str) -> Briefing:
    text = (
        f"☀️ <b>Good morning — {ctx.now:%a %d %b}</b>\n"
        f"I couldn't build today's full briefing.\n<i>{html.escape(redact(error)[:200])}</i>"
    )
    return Briefing([], "", [Message(text=text)])


def wait_for_send_time(ctx: Context) -> None:
    """Cron fires a bit early; hold until send_time if that is within max_wait_minutes."""
    hh, mm = map(int, ctx.config.send_time.split(":"))
    target = ctx.now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    wait = (target - datetime.now(ctx.config.tz)).total_seconds()
    if 0 < wait <= ctx.config.max_wait_minutes * 60:
        log.info("waiting for send time", extra={"secs": int(wait)})
        time.sleep(wait)


def run_briefing(
    ctx: Context,
    tg: TelegramClient | None,
    *,
    dry_run: bool = False,
    force: bool = False,
    only: str | None = None,
) -> int:
    """Returns the number of messages sent/printed (0 if skipped)."""
    today = ctx.today.isoformat()
    if not force and not dry_run:
        if ctx.store.get_state("skip_date") == today:
            log.info("skipping: /skip was requested for today")
            ctx.store.set_state("skip_date", None)
            ctx.store.set_state("skipped_date", today)
            return 0
        if ctx.store.get_state("last_sent_date") == today:
            log.info("skipping: already sent today")
            return 0

    try:
        briefing = build_briefing(ctx, only)
    except Exception as exc:  # noqa: BLE001 - never fail to send
        log.exception("briefing build failed; sending minimal fallback")
        briefing = minimal_briefing(ctx, str(exc))

    if dry_run or tg is None:
        print(to_plain(briefing.messages))
        return len(briefing.messages)

    for section in briefing.sections:  # persist items first so button callbacks can resolve them
        for item in section.items:
            ctx.store.remember_item(item.id, item.meta)
    for msg in briefing.messages:
        tg.send(msg)
    if not only:
        ctx.store.set_state("last_sent_date", today)
    ctx.store.prune()
    if briefing.failed:
        log.warning("sent with failed sections", extra={"failed": ",".join(briefing.failed)})
    return len(briefing.messages)


def tomorrow(now: datetime) -> str:
    return (now + timedelta(days=1)).date().isoformat()
