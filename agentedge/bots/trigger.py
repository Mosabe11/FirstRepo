"""
bots/trigger.py
---------------
High-frequency scalping bot.

For each asset in the watchlist:
  1. Fetch fresh 1m and 5m candles (cached)
  2. Run every enabled scalping strategy on the appropriate timeframe
  3. If any strategy fires a signal with edge >= TRIGGER_EDGE,
     route it through the position manager (which handles AI confirmation,
     risk checks, sizing, and execution)
  4. Respect cooldowns enforced by the risk manager

This is the bot that uses the 4 strategies from the PDF.
"""

from __future__ import annotations
import time
import logging
import threading

from config import settings
from core import market_data
from execution.manager import position_manager
from storage.watchlist_runtime import registry
from strategies import scalping_strategies

logger = logging.getLogger(__name__)

def _check_higher_tf_trend(asset_cfg, direction):
    """AI-driven multi-timeframe analysis."""
    try:
        from council.market_analyst import check_alignment
        from core import market_data
        allowed, reason = check_alignment(
            asset_cfg.symbol, asset_cfg.asset_class, direction,
            market_data.fetch_ohlcv,
        )
        return allowed, reason
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"MarketAnalyst error: {e}")
        return True, "AI error"



class TriggerBot:
    name = "trigger"

    def __init__(self):
        self._stop = threading.Event()
        # cache last candle fetch per (asset, timeframe) to refresh every ~30s
        self._last_candles: dict[tuple[str, str], tuple[float, list]] = {}

    def stop(self):
        self._stop.set()

    def run(self):
        if not scalping_strategies:
            logger.warning("No scalping strategies enabled — trigger bot exiting")
            return
        logger.info(f"Trigger bot started ({len(scalping_strategies)} strategies)")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception(f"Trigger tick error: {e}")
            self._stop.wait(settings.TRIGGER_INTERVAL)
        logger.info("Trigger bot stopped")

    def _get_candles(self, asset_class: str, exchange_symbol: str,
                     timeframe: str, limit: int = 80) -> list[list]:
        key = (exchange_symbol, timeframe)
        now = time.time()
        cached = self._last_candles.get(key)
        ttl = 30 if timeframe == "1m" else 60
        if cached and now - cached[0] < ttl:
            return cached[1]
        data = market_data.fetch_ohlcv(asset_class, exchange_symbol, timeframe, limit)
        if data:
            self._last_candles[key] = (now, data)
        return data


    def _get_ai_signals(self, watchlist):
        from core.signal import Signal
        import random
        signals = []
        sample = random.sample(list(watchlist), min(4, len(watchlist)))
        for cfg in sample:
            try:
                result = ai_generate_signal(cfg.symbol, cfg.asset_class, market_data.fetch_ohlcv)
                if not result or not result.get("should_trade"):
                    continue
                edge = float(result.get("edge", 60))
                if edge < 55:
                    continue
                sig = Signal(
                    asset=cfg.symbol,
                    direction=result.get("direction", "LONG"),
                    edge=edge,
                    price=result["current_price"],
                    strategy="ai_strategist",
                    timeframe="multi",
                    reason="AI: " + result.get("reasoning", "")[:80],
                    stop_loss=result["suggested_sl"],
                    take_profit=result["suggested_tp"],
                )
                signals.append((sig, cfg, edge))
            except Exception as e:
                logger.debug("AI signal failed: " + str(e))
        return signals

    def _tick(self):
        assets = registry.all()
        for cfg in assets:
            if self._stop.is_set():
                return
            if position_manager.has_position_on(cfg.symbol):
                # cooldown is still enforced inside try_open if needed
                continue

            # group strategies by timeframe — fetch once per timeframe
            for strat in scalping_strategies:
                allowed = getattr(strat, "allowed_classes", None)
                if allowed and cfg.asset_class not in allowed:
                    continue
                tf = strat.timeframe
                candles = self._get_candles(cfg.asset_class, cfg.exchange_symbol, tf)
                if len(candles) < strat.min_candles:
                    continue
                try:
                    signal = strat.evaluate(cfg.symbol, candles)
                except Exception as e:
                    logger.warning(f"{strat.name} on {cfg.symbol} threw: {e}")
                    continue
                if not signal.is_actionable:
                    continue
                if signal.edge < settings.TRIGGER_EDGE:
                    continue
                ok, msg = position_manager.try_open(signal, cfg, use_ai=True)
                if ok:
                    logger.info(f"[{strat.name}] {cfg.symbol} {signal.direction} "
                                f"edge={signal.edge:.0f} → {msg}")
                    break  # one strategy per asset per tick
                else:
                    logger.debug(f"[{strat.name}] {cfg.symbol} rejected: {msg}")
