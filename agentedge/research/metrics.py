"""
research/metrics.py — the full quant performance metric set.

Formulas adapted from the quant-trading-backtesting skill. Pure stdlib so it
runs anywhere. `compute(pnls)` returns the dict; `verdict(m)` returns the
honest interpretation lines. Win rate alone is meaningless — this is the
non-negotiable set: Profit Factor, expectancy, R:R, break-even win rate,
Sharpe, Sortino, max drawdown.
"""
from __future__ import annotations
import math


def compute(pnls: list[float]) -> dict | None:
    n = len(pnls)
    if n == 0:
        return None
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)

    win_rate = len(wins) / n
    avg_win = (gross_win / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    rr = (avg_win / avg_loss) if avg_loss else float("inf")
    be_wr = (avg_loss / (avg_win + avg_loss)) if (avg_win + avg_loss) else 0.0
    pf = (gross_win / gross_loss) if gross_loss else float("inf")
    expectancy = net / n

    equity = peak = max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    mean = net / n
    var = sum((p - mean) ** 2 for p in pnls) / n if n > 1 else 0.0
    std = math.sqrt(var)
    downside = [p for p in pnls if p < mean]
    dvar = sum((p - mean) ** 2 for p in downside) / n if downside else 0.0
    dstd = math.sqrt(dvar)
    sharpe = (mean / std) if std else 0.0
    sortino = (mean / dstd) if dstd else 0.0

    def longest(sign: int) -> int:
        best = cur = 0
        for p in pnls:
            if (p > 0) == (sign > 0) and p != 0:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate, "profit_factor": pf, "expectancy": expectancy,
        "net_pnl": net, "gross_win": gross_win, "gross_loss": gross_loss,
        "avg_win": avg_win, "avg_loss": avg_loss, "rr": rr,
        "break_even_wr": be_wr, "max_drawdown": max_dd,
        "sharpe_per_trade": sharpe, "sortino_per_trade": sortino,
        "longest_win_streak": longest(1), "longest_loss_streak": longest(-1),
    }


def verdict(m: dict) -> list[str]:
    pf, lines = m["profit_factor"], []
    if pf < 1.0:
        lines.append(f"LOSING: Profit Factor {pf:.2f} < 1.0 — no edge. Structural change "
                     "needed (fewer assets / higher timeframe / different signal), not tuning.")
    elif pf < 1.2:
        lines.append(f"MARGINAL: PF {pf:.2f}. Barely positive; likely negative after real costs.")
    elif pf >= 3 and m["trades"] < 100:
        lines.append(f"SUSPICIOUS: PF {pf:.2f} on {m['trades']} trades — check look-ahead/overfit.")
    else:
        lines.append(f"EDGE PRESENT: PF {pf:.2f}. Confirm OOS + costs before sizing up.")
    if m["win_rate"] < m["break_even_wr"]:
        lines.append(f"R:R PROBLEM: win rate {m['win_rate']*100:.1f}% < break-even "
                     f"{m['break_even_wr']*100:.1f}% at R:R {m['rr']:.2f}. Fix reward/risk, not win rate.")
    if m["trades"] < 30:
        lines.append("Sample < 30: anecdotal — do not conclude.")
    elif m["trades"] < 100:
        lines.append("Sample 30-100: tentative; check it isn't 1-2 outliers.")
    return lines


def fmt_row(label: str, m: dict | None) -> str:
    if not m:
        return f"{label:<22} | no trades"
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    rr = m["rr"]
    rr_s = "inf" if rr == float("inf") else f"{rr:.2f}"
    return (f"{label:<22} | {m['trades']:>4} | {m['win_rate']*100:>5.1f}% | "
            f"{pf_s:>5} | {m['expectancy']:>+8.4f} | {m['net_pnl']:>+9.2f} | "
            f"{rr_s:>5} | {m['break_even_wr']*100:>5.1f}% | {m['max_drawdown']:>+9.2f}")


HEADER = (f"{'strategy / variant':<22} | {'N':>4} | {'WR':>6} | {'PF':>5} | "
          f"{'expect.':>8} | {'net':>9} | {'R:R':>5} | {'beWR':>6} | {'maxDD':>9}")
