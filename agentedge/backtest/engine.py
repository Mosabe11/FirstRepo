"""
backtest/engine.py — Quick backtest engine + adaptive strategy weights.

For each (asset, strategy) pair, replays the strategy over the last N candles
and computes win rate. Cached so it runs at most every 6 hours per pair.

The Trigger bot uses get_strategy_weight() to multiply each strategy's edge
score — strategies that have historically worked on an asset get higher
priority.
"""
import time
import logging
import threading
import json
from pathlib import Path

import numpy as np

from config import settings
from core import market_data
from strategies import scalping_strategies

logger = logging.getLogger(__name__)

WEIGHTS_PATH = settings.DATA_DIR / "strategy_weights.json"
_lock = threading.Lock()
_weights = {}
_last_run = {}


def _load():
    global _weights
    if WEIGHTS_PATH.exists():
        try:
            with open(WEIGHTS_PATH) as f:
                data = json.load(f)
            _weights = data.get("weights", {})
            for k, v in data.get("last_run", {}).items():
                _last_run[k] = v
            logger.info(f"Loaded {len(_weights)} strategy weights")
        except Exception as e:
            logger.warning(f"Failed to load weights: {e}")


def _save():
    try:
        with open(WEIGHTS_PATH, "w") as f:
            json.dump({"weights": _weights, "last_run": _last_run}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save weights: {e}")


def _simulate_strategy(strategy, candles, lookback_bars=200):
    """
    Walk the candles forward, evaluate the strategy at each step,
    and compute the simulated PnL.
    Simplified: holds for max 20 bars or until TP/SL hit.
    """
    if len(candles) < strategy.min_candles + 20:
        return None

    arr = np.array(candles, dtype=float)
    trades = []
    i = strategy.min_candles
    cooldown_until = 0

    while i < len(arr) - 20:
        if i < cooldown_until:
            i += 1
            continue

        # Evaluate strategy on slice up to i
        window = arr[max(0, i-strategy.min_candles):i+1].tolist()
        try:
            sig = strategy.evaluate("BACKTEST", window)
        except Exception:
            i += 1
            continue

        if not sig.is_actionable or sig.edge < 60:
            i += 1
            continue

        entry = float(arr[i, 4])
        sl = sig.stop_loss or (entry * 0.98 if sig.direction == "LONG" else entry * 1.02)
        tp = sig.take_profit or (entry * 1.02 if sig.direction == "LONG" else entry * 0.98)

        # Walk forward up to 20 bars looking for TP or SL hit
        exit_price = None
        exit_reason = "TIMEOUT"
        for j in range(i+1, min(i+21, len(arr))):
            high = float(arr[j, 2])
            low = float(arr[j, 3])
            if sig.direction == "LONG":
                if low <= sl:
                    exit_price = sl
                    exit_reason = "SL"
                    break
                if high >= tp:
                    exit_price = tp
                    exit_reason = "TP"
                    break
            else:
                if high >= sl:
                    exit_price = sl
                    exit_reason = "SL"
                    break
                if low <= tp:
                    exit_price = tp
                    exit_reason = "TP"
                    break

        if exit_price is None:
            exit_price = float(arr[min(i+20, len(arr)-1), 4])

        pnl_pct = ((exit_price - entry) / entry) if sig.direction == "LONG" else ((entry - exit_price) / entry)
        trades.append({"pnl_pct": pnl_pct, "win": pnl_pct > 0, "reason": exit_reason})

        i += 20  # skip forward
        cooldown_until = i + 5

    if not trades:
        return None

    wins = sum(1 for t in trades if t["win"])
    total = len(trades)
    avg_pnl = sum(t["pnl_pct"] for t in trades) / total
    return {
        "trades": total,
        "wins": wins,
        "win_rate": wins / total,
        "avg_pnl_pct": avg_pnl,
    }


def run_backtest(asset_class, exchange_symbol, asset_symbol):
    """Run all scalping strategies on this asset, update weights."""
    candles = market_data.fetch_ohlcv(asset_class, exchange_symbol, "5m", 500)
    if len(candles) < 100:
        return

    for strat in scalping_strategies:
        if strat.timeframe == "5m":
            cs = candles
        else:
            cs = market_data.fetch_ohlcv(asset_class, exchange_symbol, strat.timeframe, 500)
            if len(cs) < 100:
                continue

        result = _simulate_strategy(strat, cs)
        if not result:
            continue

        key = f"{asset_symbol}:{strat.name}"
        wr = result["win_rate"]
        # Map win rate to weight: 0.3 wr → 0.7x, 0.5 wr → 1.0x, 0.7 wr → 1.3x
        weight = max(0.6, min(1.4, 0.4 + wr * 1.2))
        with _lock:
            _weights[key] = {
                "weight": round(weight, 3),
                "win_rate": round(wr, 3),
                "trades": result["trades"],
                "avg_pnl_pct": round(result["avg_pnl_pct"] * 100, 3),
            }
            _last_run[asset_symbol] = time.time()
        logger.info(
            f"Backtest {asset_symbol} {strat.name}: "
            f"{result['trades']} trades, WR {wr:.2%}, weight {weight:.2f}"
        )

    _save()


def get_strategy_weight(asset, strategy):
    """Get the adaptive weight for this (asset, strategy). Default 1.0."""
    with _lock:
        key = f"{asset}:{strategy}"
        info = _weights.get(key)
        return info["weight"] if info else 1.0


def should_rerun(asset_symbol):
    """Re-run backtest every 6 hours per asset."""
    last = _last_run.get(asset_symbol, 0)
    return time.time() - last > 6 * 3600


def get_all_weights():
    with _lock:
        return dict(_weights)


# Load on import
_load()
