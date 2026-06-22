"""
research/run_tf.py — does a higher timeframe let the regime_trend edge clear costs?

Hypothesis (from the skill, step 5): the 1h edge is real but too thin vs fixed
per-trade fees. Bigger bars => bigger average move per trade => the same bps
cost is a smaller fraction. Test 1h / 4h / 1d, in-sample AND out-of-sample,
WITH realistic costs. Same strategy, same exit, same params — only timeframe
changes. (Caveat: testing 3 timeframes is mild multiple-testing; OOS must hold.)

Run:  python -m research.run_tf
"""
from __future__ import annotations

from research import data, metrics
from research.engine import backtest
from research.exits import RMultipleExit
from research.strategies import make_regime_trend

ASSETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
SPLIT = 0.70
# (timeframe, bars) — fewer bars at higher TF (exchange history limits)
TFS = [("1h", 4000), ("4h", 3000), ("1d", 1200)]


def split(c):
    k = int(len(c) * SPLIT)
    return c[:k], c[k:]


def main():
    regime = make_regime_trend(adx_min=22.0, donchian=20)
    rows = []
    for tf, bars in TFS:
        is_p, oos_p = [], []
        for sym in ASSETS:
            c = data.load(sym, tf, bars)
            cis, coos = split(c)
            is_p += backtest(cis, regime, RMultipleExit(), fee_bps=7.5)["pnls"]
            oos_p += backtest(coos, regime, RMultipleExit(), fee_bps=7.5)["pnls"]
        rows.append((tf, metrics.compute(is_p), metrics.compute(oos_p)))

    for scope, idx in (("IN-SAMPLE", 1), ("OUT-OF-SAMPLE", 2)):
        print("\n" + "=" * 110)
        print(f"  regime_trend(adx>=22,don=20) + Rmult  |  {scope}  |  costs 7.5bps/side+2bps slip")
        print("=" * 110)
        print("  " + metrics.HEADER)
        print("  " + "-" * 106)
        for tf, m_is, m_oos in rows:
            print("  " + metrics.fmt_row(f"{tf:<4} timeframe", m_is if idx == 1 else m_oos))

    print("\n" + "=" * 110)
    print("  OUT-OF-SAMPLE VERDICT (with costs)")
    print("=" * 110)
    for tf, _m_is, m_oos in rows:
        print(f"\n  >> {tf} timeframe")
        if m_oos:
            for line in metrics.verdict(m_oos):
                print(f"       - {line}")
        else:
            print("       - no OOS trades")


if __name__ == "__main__":
    main()
