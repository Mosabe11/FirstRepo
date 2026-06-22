"""
main.py
-------
AgentEdge v2 entry point.

Boots every enabled bot in its own daemon thread, sets up logging with
daily rotation, installs SIGINT/SIGTERM handlers so the process shuts
down cleanly, and blocks on a shared shutdown event.

Usage:
    python main.py

That's it. Configuration is read from .env at import time
(see config/settings.py).
"""

from __future__ import annotations
import sys
import time
import signal
import logging
import threading
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

from config import settings
from bots.monitor import MonitorBot
from bots.trigger import TriggerBot
from bots.auto_scanner import AutoScannerBot
from bots.discovery import DiscoveryBot
from bots.cleaner import CleanerBot
from bots.backtest_bot import BacktestBot
from bots.levels_bot import LevelsBot
from api.server import HttpServerBot
from api.telegram import TelegramBot


# ===================== logging =====================
def setup_logging():
    settings.LOGS_DIR.mkdir(exist_ok=True)
    log_path = settings.LOGS_DIR / "agentedge.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # avoid double-installing if called twice (e.g. in REPL)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # rotating file handler, daily, keep N days
    fh = TimedRotatingFileHandler(
        log_path, when="midnight", interval=1,
        backupCount=settings.LOG_RETENTION_DAYS, utc=True,
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # mute noisy libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)


# ===================== bot wiring =====================
def build_bots(shutdown_event: threading.Event):
    """Return list of (name, bot_instance) for every enabled bot."""
    bots = []

    if settings.ENABLE_MONITOR:
        bots.append(("monitor", MonitorBot()))
    if settings.ENABLE_TRIGGER:
        bots.append(("trigger", TriggerBot()))
    if settings.ENABLE_AUTO_SCANNER:
        bots.append(("auto_scanner", AutoScannerBot()))
    if settings.ENABLE_DISCOVERY:
        bots.append(("discovery", DiscoveryBot()))
    if settings.ENABLE_CLEANER:
        bots.append(("cleaner", CleanerBot()))
    if getattr(settings, "ENABLE_BACKTEST_BOT", True):
        bots.append(("backtest", BacktestBot()))
    if getattr(settings, "ENABLE_LEVELS_BOT", True):
        bots.append(("levels", LevelsBot()))

    # HTTP server is always on
    bots.append(("http_server", HttpServerBot()))

    # Telegram only if configured
    tg = TelegramBot(shutdown_event)
    if tg.enabled():
        bots.append(("telegram", tg))

    return bots


def start_bots(bots) -> dict[str, threading.Thread]:
    threads: dict[str, threading.Thread] = {}
    for name, bot in bots:
        t = threading.Thread(target=bot.run, name=f"bot-{name}", daemon=True)
        t.start()
        threads[name] = t
        logging.info(f"Started bot: {name}")
        time.sleep(0.05)  # stagger boot to avoid log interleave
    return threads


def shutdown_bots(bots):
    for name, bot in bots:
        try:
            bot.stop()
            logging.info(f"Sent stop signal: {name}")
        except Exception as e:
            logging.warning(f"Error stopping {name}: {e}")


# ===================== entry =====================
def main():
    setup_logging()
    log = logging.getLogger("main")

    # config sanity
    errors = settings.validate()
    for e in errors:
        log.warning(f"Config: {e}")
    if any("REFUSING TO START LIVE" in e for e in errors):
        log.error("Refusing to start in LIVE mode without MEXC credentials")
        sys.exit(1)

    # banner
    print(settings.summary())
    if settings.LIVE_MODE:
        print(
            "⚠ ⚠ ⚠   LIVE MODE — real money will be traded on MEXC.   ⚠ ⚠ ⚠\n"
            "         Starting in 5 seconds. Press Ctrl-C to abort.\n"
        )
        time.sleep(5)

    shutdown_event = threading.Event()

    def handle_signal(signum, frame):
        log.info(f"Received signal {signum}, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    bots = build_bots(shutdown_event)
    threads = start_bots(bots)
    log.info(f"All {len(bots)} bots running")

    # block until shutdown
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(1.0)
    except KeyboardInterrupt:
        shutdown_event.set()

    log.info("Shutdown initiated — stopping bots...")
    shutdown_bots(bots)

    # give threads a few seconds to finish
    deadline = time.time() + 8
    for name, t in threads.items():
        remaining = max(0.5, deadline - time.time())
        t.join(timeout=remaining)
        if t.is_alive():
            log.warning(f"Bot {name} did not stop cleanly")

    log.info("Goodbye.")


if __name__ == "__main__":
    main()
