"""
bots/backtest_bot.py — Periodically re-runs backtests to update strategy weights.
"""
import logging
import threading

from config import settings
from storage.watchlist_runtime import registry

logger = logging.getLogger(__name__)


class BacktestBot:
    name = "backtest"

    def __init__(self):
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        if not settings.ENABLE_ADAPTIVE_WEIGHTS:
            logger.info("Adaptive weights disabled — backtest bot exiting")
            return
        logger.info("Backtest bot started (refresh every 6h)")
        # Initial run after 2 min to let other bots warm up
        self._stop.wait(120)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception(f"Backtest tick error: {e}")
            self._stop.wait(settings.BACKTEST_INTERVAL)
        logger.info("Backtest bot stopped")

    def _tick(self):
        from backtest.engine import run_backtest, should_rerun
        from core.notify import tg_send
        assets = registry.all()
        ran = 0
        for cfg in assets:
            if self._stop.is_set():
                return
            if not should_rerun(cfg.symbol):
                continue
            try:
                run_backtest(cfg.asset_class, cfg.exchange_symbol, cfg.symbol)
                ran += 1
            except Exception as e:
                logger.warning(f"Backtest failed {cfg.symbol}: {e}")
        if ran:
            tg_send(f"📊 *Backtest cycle complete* — refreshed weights for {ran} assets", category="general")
            logger.info(f"Backtest cycle: refreshed {ran} assets")
