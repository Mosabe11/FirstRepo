"""
research/run_live_strategy.py — validate the ACTUAL shipped strategy class.

Runs strategies.regime_trend.RegimeTrendStrategy (the real live code, not a
research re-implementation) through the cost-aware, no-look-ahead engine on
daily data. If the live class is wired correctly its numbers should match the
research prototype (PF ~1.2 OOS). This is the bridge that proves the deployed
code == the validated code.

Run:  python -m research.run_live_strategy
"""
from __future__ import annotations
import numpy as np

from research import data, metrics
from research.engine import backtest
from research.exits import RMultipleExit
from strategies.regime_trend import RegimeTrendStrategy

ASSETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
SPLIT = 0.70
_strat = RegimeTrendStrategy()


def live_signal(arr: np.ndarray) -> dict | None:
    """Adapt the live Strategy.evaluate() into the engine's signal_fn contract."""
    sig = _strat.evaluate("BACKTEST", arr.tolist())
    if not sig.is_actionable:
        return None
    return {"direction": sig.direction,
            "atr": sig.extras.get("atr"),
            "reason": sig.reason}


def main():
    is_p, oos_p = [], []
    for sym in ASSETS:
        c = data.load(sym, "1d", 1200)
        k = int(len(c) * SPLIT)
        is_p += backtest(c[:k], live_signal, RMultipleExit(), fee_bps=7.5)["pnls"]
        oos_p += backtest(c[k:], live_signal, RMultipleExit(), fee_bps=7.5)["pnls"]

    print("LIVE RegimeTrendStrategy class — daily, costs 7.5bps/side + 2bps slip")
    print(metrics.HEADER)
    print(metrics.fmt_row("IN-SAMPLE", metrics.compute(is_p)))
    print(metrics.fmt_row("OUT-OF-SAMPLE", metrics.compute(oos_p)))
    print("\nOOS verdict:")
    m = metrics.compute(oos_p)
    if m:
        for line in metrics.verdict(m):
            print("  -", line)


if __name__ == "__main__":
    main()
