"""
research/run.py — the honest verdict harness.

Run from the agentedge dir:   python -m research.run

It pulls real hourly candles (binance, public), splits each asset into
in-sample (first 70%) and out-of-sample (last 30%), and runs every
strategy x exit-policy combination through the cost-aware backtester.

The point is comparison, not a single number:
  1. baseline vwap_macd + LEGACY exit   = (approximately) today's live system
  2. baseline vwap_macd + R-MULTIPLE exit = does fixing the exit alone help?
  3. regime_trend       + R-MULTIPLE exit = does a regime-filtered signal help?
And the new strategy is run with and without costs to expose cost impact.
"""
from __future__ import annotations

from research import data, metrics
from research.engine import backtest
from research.exits import LegacyStrangleExit, RMultipleExit
from research.strategies import vwap_macd_signal, make_regime_trend

ASSETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TF = "1h"
BARS = 4000
SPLIT = 0.70


def split(candles):
    k = int(len(candles) * SPLIT)
    return candles[:k], candles[k:]


def run_combo(label, signal_fn, exit_factory, fee_bps=7.5):
    """Aggregate per-trade P&L across all assets, separately for IS and OOS."""
    is_pnls, oos_pnls = [], []
    for sym in ASSETS:
        candles = data.load(sym, TF, BARS)
        c_is, c_oos = split(candles)
        is_pnls += backtest(c_is, signal_fn, exit_factory(), fee_bps=fee_bps)["pnls"]
        oos_pnls += backtest(c_oos, signal_fn, exit_factory(), fee_bps=fee_bps)["pnls"]
    return label, metrics.compute(is_pnls), metrics.compute(oos_pnls)


def main():
    print(f"Loading {ASSETS} {TF} ({BARS} bars each) ...")
    for sym in ASSETS:
        c = data.load(sym, TF, BARS)
        import time
        print(f"  {sym}: {len(c)} bars "
              f"{time.strftime('%Y-%m-%d', time.gmtime(c[0][0]/1000))} -> "
              f"{time.strftime('%Y-%m-%d', time.gmtime(c[-1][0]/1000))}")

    regime = make_regime_trend(adx_min=22.0, donchian=20)

    combos = [
        run_combo("vwap_macd + LEGACY", vwap_macd_signal, LegacyStrangleExit),
        run_combo("vwap_macd + Rmult", vwap_macd_signal, lambda: RMultipleExit()),
        run_combo("regime_trend + Rmult", regime, lambda: RMultipleExit()),
        run_combo("regime_trend (NO costs)", regime, lambda: RMultipleExit(), fee_bps=0.0),
    ]

    for scope, idx in (("IN-SAMPLE", 1), ("OUT-OF-SAMPLE", 2)):
        print("\n" + "=" * 110)
        print(f"  {scope}   (binance {TF}, fee 7.5bps/side + 2bps slip unless noted, "
              f"ATR1.5 stop / 2.5R target)")
        print("=" * 110)
        print("  " + metrics.HEADER)
        print("  " + "-" * 106)
        for label, m_is, m_oos in combos:
            m = m_is if idx == 1 else m_oos
            print("  " + metrics.fmt_row(label, m))

    print("\n" + "=" * 110)
    print("  HONEST VERDICT  (out-of-sample, with costs — the only result that matters)")
    print("=" * 110)
    for label, _m_is, m_oos in combos:
        if "NO costs" in label:
            continue
        print(f"\n  >> {label}")
        if m_oos:
            for line in metrics.verdict(m_oos):
                print(f"       - {line}")
        else:
            print("       - no out-of-sample trades")


if __name__ == "__main__":
    main()
