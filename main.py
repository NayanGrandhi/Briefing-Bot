"""CLI entrypoint.

  python main.py run  [--dry-run] [--force] [--wait] [--section NAME] [--no-ai] [--demo]
  python main.py poll [--dry-run]        # handle Telegram buttons/commands, then exit
Bare `python main.py --dry-run` is shorthand for `run --dry-run`.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from core import demo as demo_mod
from core.ai import AIService
from core.config import ConfigError, load_config
from core.context import Context
from core.env import load_dotenv
from core.log import setup_logging
from core.runner import run_briefing, wait_for_send_time
from core.secrets import redact
from core.store import Store
from providers.llm import build_llm
from providers.telegram import TelegramClient

log = logging.getLogger("main")

EXIT_ALERTED = 2  # failed, but the Telegram alert was delivered (workflow skips its own alert)
EXIT_UNALERTED = 3  # failed and no alert could be sent


def parse_args(argv: list[str]) -> argparse.Namespace:
    if not argv or argv[0] not in ("run", "poll", "-h", "--help"):
        argv = ["run", *argv]
    p = argparse.ArgumentParser(description="Daily briefing bot")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.yaml", help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", parents=[common], help="build and send the briefing")
    run.add_argument("--dry-run", action="store_true", help="print to terminal, send nothing")
    run.add_argument("--force", action="store_true", help="ignore /skip and already-sent checks")
    run.add_argument("--wait", action="store_true", help="hold until send_time (cron mode)")
    run.add_argument("--section", help="only build this section")
    run.add_argument("--no-ai", action="store_true", help="disable the LLM (test fallback)")
    run.add_argument("--demo", action="store_true", help="offline fixtures + stub LLM")
    poll = sub.add_parser(
        "poll", parents=[common], help="process pending Telegram commands/button presses"
    )
    poll.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def telegram_from_env() -> TelegramClient | None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    return TelegramClient(token, chat) if token and chat else None


def _store_for(path: str, dry_run: bool) -> Store:
    """Dry runs work on a temp copy of the DB so they never alter real state."""
    if dry_run:
        tmp = Path(tempfile.mkdtemp()) / "state.db"
        if Path(path).is_file():
            shutil.copy(path, tmp)
        return Store(tmp)
    return Store(path)


def execute(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    now = datetime.now(cfg.tz)
    dry = bool(getattr(args, "dry_run", False))
    demo = bool(getattr(args, "demo", False))
    if demo:
        demo_mod.install(cfg, now)
        dry = True
    store = _store_for(cfg.db_path, dry)
    llm = None if getattr(args, "no_ai", False) else build_llm(cfg.llm)
    if demo and not getattr(args, "no_ai", False):
        llm = demo_mod.DemoLLM()
    ai = AIService(llm, store, cfg.llm.daily_call_budget, cfg.llm.cache_ttl_hours, today=now.date())
    ctx = Context(cfg, store, ai, now)
    tg = None if dry else telegram_from_env()
    if not dry and tg is None:
        raise ConfigError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (or use --dry-run).")

    try:
        if args.cmd == "run":
            if args.wait and not dry:
                wait_for_send_time(ctx)
                ctx.now = datetime.now(cfg.tz)
            run_briefing(ctx, tg, dry_run=dry, force=args.force, only=args.section)
        else:
            from bot.poller import poll

            poll(ctx, tg, dry_run=dry)
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return execute(args)
    except BaseException as exc:  # noqa: BLE001 - last line of defence: alert, then fail loudly
        if isinstance(exc, SystemExit | KeyboardInterrupt):
            raise
        log.exception("run failed")
        tg = telegram_from_env()
        alerted = bool(
            tg and tg.alert(f"Run failed ({args.cmd}): {type(exc).__name__}: {redact(str(exc))}")
        )
        return EXIT_ALERTED if alerted else EXIT_UNALERTED


if __name__ == "__main__":
    sys.exit(main())
