"""
bots/auto_scanner.py
--------------------
Periodic 1h scan across the watchlist using the legacy swing strategy.
Slower cadence (every couple of minutes); base assets get priority.
"""

from __future__ import annotations
import time
import logging
import threading

from config import settings
from core import market_data
from execution.manager import position_manager
from storage.watchlist_runtime import registry
from strategies import swing_strategies

logger = logging.getLogger(__name__)


class AutoScannerBot:
    name = "auto_scanner"

    def __init__(self):
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        if not swing_strategies:
            logger.info("No swing strategies enabled — auto_scanner exiting")
            return
        logger.info("Auto-scanner started")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception(f"AutoScanner tick error: {e}")
            self._stop.wait(settings.AUTO_SCAN_INTERVAL)
        logger.info("Auto-scanner stopped")

    def _tick(self):
        # Priority: base assets first
        assets = sorted(registry.all(), key=lambda a: (not a.is_base, a.symbol))
        for cfg in assets:
            if self._stop.is_set():
                return
            if position_manager.has_position_on(cfg.symbol):
                continue
            candles = market_data.fetch_ohlcv(
                cfg.asset_class, cfg.exchange_symbol, "1h", 100
            )
            if not candles:
                continue
            for strat in swing_strategies:
                try:
                    signal = strat.evaluate(cfg.symbol, candles)
                except Exception as e:
                    logger.warning(f"{strat.name} on {cfg.symbol} threw: {e}")
                    continue
                if not signal.is_actionable:
                    continue
                if signal.edge < settings.AUTO_ENTRY:
                    continue
                ok, msg = position_manager.try_open(signal, cfg, use_ai=True)
                if ok:
                    logger.info(f"[{strat.name}] {cfg.symbol} {signal.direction} "
                                f"edge={signal.edge:.0f} → {msg}")
                    break
