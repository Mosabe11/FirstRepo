"""
core/learning.py
----------------
Disciplined self-learning from the agent's OWN realized trades.

The old adaptive-weights layer (backtest/engine.py) boosted a strategy off as
few as 4-6 trades — that is fitting noise, and the live logs proved it produced
nothing. This module learns the right way:

  * It only acts after a MINIMUM SAMPLE (default 20 closed trades for a
    given (asset, strategy)). Below that it stays neutral — "I don't know yet."
  * It judges an edge by EXPECTANCY (avg P&L/trade, after fees) and by the
    WILSON LOWER BOUND on win rate — the 95% pessimistic estimate, which
    refuses to be fooled by a lucky streak on a small sample.
  * Verdicts: DISABLE (statistically losing -> stop trading it),
    KEEP (neutral), BOOST (statistically winning -> size up, capped at 1.4x).

This is the honest version of "the agent learns": it can only ever conclude
an edge exists once the evidence clears a real bar — and it will switch a
strategy/asset OFF on its own when the evidence says it's losing.
"""
from __future__ import annotations
import math

MIN_SAMPLE = 20          # below this: stay neutral, never act on noise
MAX_BOOST = 1.4          # cap how far a winner can be sized up
MIN_WEIGHT = 0.0         # DISABLE -> 0 (do not trade)
Z = 1.96                 # 95% confidence


def wilson_lower_bound(wins: int, n: int, z: float = Z) -> float:
    """Pessimistic (lower) 95% bound on the true win rate. Small/lucky samples
    get pulled toward 0, so a 4/5 streak does NOT read as an 80% edge."""
    if n == 0:
        return 0.0
    phat = wins / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def summarize(pnls: list[float]) -> dict:
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    rr = (avg_win / avg_loss) if avg_loss else float("inf")
    # break-even win rate implied by this R:R
    be_wr = (avg_loss / (avg_win + avg_loss)) if (avg_win + avg_loss) else 1.0
    return {
        "n": n,
        "wins": len(wins),
        "win_rate": (len(wins) / n) if n else 0.0,
        "wilson_lb": wilson_lower_bound(len(wins), n),
        "expectancy": (sum(pnls) / n) if n else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else float("inf"),
        "rr": rr,
        "break_even_wr": be_wr,
    }


def decision(pnls: list[float]) -> dict:
    """Return {enabled, weight, verdict, reason, stats} for an (asset, strategy)
    based purely on its realized trade history."""
    s = summarize(pnls)
    n = s["n"]
    if n < MIN_SAMPLE:
        return {"enabled": True, "weight": 1.0, "verdict": "LEARNING",
                "reason": f"only {n}/{MIN_SAMPLE} trades — not enough to judge",
                "stats": s}

    expectancy = s["expectancy"]
    # Statistically losing: negative expectancy AND the (pessimistic) win rate
    # can't clear the break-even win rate this R:R demands -> switch it OFF.
    if expectancy <= 0 and s["wilson_lb"] < s["break_even_wr"]:
        return {"enabled": False, "weight": MIN_WEIGHT, "verdict": "DISABLE",
                "reason": (f"losing: expectancy {expectancy:+.3f}, "
                           f"WR lower-bound {s['wilson_lb']*100:.0f}% < "
                           f"break-even {s['break_even_wr']*100:.0f}%"),
                "stats": s}

    # Statistically winning: positive expectancy AND pessimistic win rate still
    # clears break-even -> size up, scaled by how far above break-even it is.
    if expectancy > 0 and s["wilson_lb"] > s["break_even_wr"]:
        edge_margin = s["wilson_lb"] - s["break_even_wr"]
        weight = min(MAX_BOOST, 1.0 + edge_margin * 2.0)
        return {"enabled": True, "weight": round(weight, 3), "verdict": "BOOST",
                "reason": (f"winning: expectancy {expectancy:+.3f}, "
                           f"WR lower-bound {s['wilson_lb']*100:.0f}% > "
                           f"break-even {s['break_even_wr']*100:.0f}%"),
                "stats": s}

    return {"enabled": True, "weight": 1.0, "verdict": "KEEP",
            "reason": f"neutral: expectancy {expectancy:+.3f}", "stats": s}
