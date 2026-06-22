"""
research/engine.py — event-driven backtester. No look-ahead by construction.

Loop invariant: at bar i the strategy sees candles[:i+1] only (the closed bar
and history). A signal on bar i is FILLED at bar i+1's OPEN — you can never
trade on information you didn't have. Costs (fees + slippage) are charged on
both fills. One position at a time (flat-to-flat), which is the right unit for
measuring whether the signal+exit pair has a per-trade edge.

Returns a list of per-trade P&L in quote currency on a fixed notional, plus a
detailed trade log for inspection.
"""
from __future__ import annotations
import numpy as np

from research.exits import Trade


def backtest(candles: list[list], signal_fn, exit_policy,
             notional: float = 1000.0,
             fee_bps: float = 7.5, slippage_bps: float = 2.0,
             stop_atr_mult: float = 1.5, rr: float = 2.5,
             max_hold: int = 240, warmup: int = 60) -> dict:
    arr = np.asarray(candles, dtype=float)
    n = len(arr)
    fee = fee_bps / 10_000.0
    slip = slippage_bps / 10_000.0

    pnls: list[float] = []
    log: list[dict] = []
    i = warmup
    open_trade: Trade | None = None
    entry_notional = 0.0
    qty = 0.0
    entry_idx = 0

    while i < n:
        hi, lo, cl = arr[i, 2], arr[i, 3], arr[i, 4]

        if open_trade is not None:
            res = exit_policy.update(open_trade, hi, lo, cl)
            timed_out = (i - entry_idx) >= max_hold
            if res is None and timed_out:
                res = (cl, "TIMEOUT")
            if res is not None:
                raw_px, reason = res
                # exit slippage always worsens the fill
                exit_px = raw_px * (1 - slip) if open_trade.direction == "LONG" else raw_px * (1 + slip)
                if open_trade.direction == "LONG":
                    gross = qty * (exit_px - open_trade.entry)
                else:
                    gross = qty * (open_trade.entry - exit_px)
                exit_fee = abs(exit_px * qty) * fee
                entry_fee = entry_notional * fee
                pnl = gross - entry_fee - exit_fee
                pnls.append(pnl)
                log.append({"entry_idx": entry_idx, "exit_idx": i,
                            "direction": open_trade.direction,
                            "entry": round(open_trade.entry, 6),
                            "exit": round(exit_px, 6), "reason": reason,
                            "pnl": round(pnl, 4),
                            "r": round(open_trade.r_multiple(raw_px), 2),
                            "bars": i - entry_idx})
                open_trade = None

        if open_trade is None and i + 1 < n:
            sig = signal_fn(arr[:i + 1])
            if sig and sig.get("direction") in ("LONG", "SHORT"):
                atr = sig.get("atr") or 0.0
                fill_open = arr[i + 1, 1]
                if atr > 0 and fill_open > 0:
                    direction = sig["direction"]
                    entry = fill_open * (1 + slip) if direction == "LONG" else fill_open * (1 - slip)
                    if direction == "LONG":
                        stop = entry - stop_atr_mult * atr
                        risk = entry - stop
                        target = entry + rr * risk
                    else:
                        stop = entry + stop_atr_mult * atr
                        risk = stop - entry
                        target = entry - rr * risk
                    if risk > 0:
                        open_trade = Trade(direction=direction, entry=entry, stop=stop,
                                           target=target, atr=atr, risk=risk)
                        qty = notional / entry
                        entry_notional = notional
                        entry_idx = i + 1
                        i += 1  # fill happens on next bar; advance past it
        i += 1

    return {"pnls": pnls, "log": log}
