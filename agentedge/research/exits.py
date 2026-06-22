"""
research/exits.py — exit / trade-management policies.

Two policies, identical interface, so the backtester can run the *same*
signals through each and isolate the effect of trade management alone:

  LegacyStrangleExit  — faithful to the current execution/position.py:
                        arms break-even at +1.0% (moves stop to entry+0.2%),
                        then trails tight (0.6%). Caps winners hard while
                        losers run to the full stop. This is the inverted-R:R
                        machine the live logs show.

  RMultipleExit       — the fix: ATR stop, 2.5R target, break-even only after
                        +1.0R, then a WIDE ATR trail that only engages after
                        +1.5R. Lets winners actually reach the target.

Each policy mutates the trade's stop and returns (exit_price, reason) when an
exit triggers on the current bar, else None. Intrabar rule is pessimistic:
if both stop and target are touched in one bar, the stop is assumed first.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Trade:
    direction: str            # "LONG" / "SHORT"
    entry: float
    stop: float
    target: float
    atr: float                # ATR at entry (for ATR-based trails)
    risk: float               # |entry - stop| at entry (1R, in price)
    hwm: float = 0.0          # high-water mark (LONG) / low-water (SHORT)
    be_armed: bool = False
    trail_armed: bool = False

    def __post_init__(self):
        self.hwm = self.entry

    def r_multiple(self, price: float) -> float:
        if self.risk <= 0:
            return 0.0
        move = (price - self.entry) if self.direction == "LONG" else (self.entry - price)
        return move / self.risk

    def pct(self, price: float) -> float:
        return ((price - self.entry) if self.direction == "LONG"
                else (self.entry - price)) / self.entry


def _hit(trade: Trade, high: float, low: float):
    """Return ('SL'|'TP', price) if stop/target touched this bar, else None.
    Pessimistic: stop checked before target."""
    if trade.direction == "LONG":
        if low <= trade.stop:
            return ("STOP", trade.stop)
        if high >= trade.target:
            return ("TP", trade.target)
    else:
        if high >= trade.stop:
            return ("STOP", trade.stop)
        if low <= trade.target:
            return ("TP", trade.target)
    return None


class LegacyStrangleExit:
    """Mimics execution/position.py trade management."""
    name = "legacy_strangle"

    def update(self, t: Trade, high: float, low: float, close: float):
        hit = _hit(t, high, low)
        if hit:
            reason, px = hit
            if reason == "STOP":
                return px, ("TRAIL" if t.trail_armed else "SL")
            return px, "TP"

        pct = t.pct(close)
        if t.direction == "LONG":
            t.hwm = max(t.hwm, high)
            if not t.be_armed and pct >= 0.010:          # +1.0% -> lock entry+0.2%
                t.stop = max(t.stop, t.entry * 1.002)
                t.be_armed = True
            if t.be_armed and pct >= 0.015:               # tight 0.6% trail
                trail = t.hwm * (1 - 0.006)
                if trail > t.stop:
                    t.stop = trail
                    t.trail_armed = True
        else:
            t.hwm = min(t.hwm, low)
            if not t.be_armed and pct >= 0.010:
                t.stop = min(t.stop, t.entry * 0.998)
                t.be_armed = True
            if t.be_armed and pct >= 0.015:
                trail = t.hwm * (1 + 0.006)
                if trail < t.stop:
                    t.stop = trail
                    t.trail_armed = True
        return None


class RMultipleExit:
    """The fix: don't strangle winners. Break-even only after +1R, then a
    wide ATR trail that engages after +1.5R; otherwise ride to the 2.5R target."""
    name = "r_multiple"

    def __init__(self, be_at_r: float = 1.0, trail_at_r: float = 1.5,
                 trail_atr_mult: float = 2.0):
        self.be_at_r = be_at_r
        self.trail_at_r = trail_at_r
        self.trail_atr_mult = trail_atr_mult

    def update(self, t: Trade, high: float, low: float, close: float):
        hit = _hit(t, high, low)
        if hit:
            reason, px = hit
            if reason == "STOP":
                return px, ("TRAIL" if t.trail_armed else "SL")
            return px, "TP"

        r = t.r_multiple(close)
        if t.direction == "LONG":
            t.hwm = max(t.hwm, high)
            if not t.be_armed and r >= self.be_at_r:       # cover costs, stay in
                t.stop = max(t.stop, t.entry + 0.1 * t.risk)
                t.be_armed = True
            if r >= self.trail_at_r:                        # WIDE trail — let it run
                trail = t.hwm - self.trail_atr_mult * t.atr
                if trail > t.stop:
                    t.stop = trail
                    t.trail_armed = True
        else:
            t.hwm = min(t.hwm, low)
            if not t.be_armed and r >= self.be_at_r:
                t.stop = min(t.stop, t.entry - 0.1 * t.risk)
                t.be_armed = True
            if r >= self.trail_at_r:
                trail = t.hwm + self.trail_atr_mult * t.atr
                if trail < t.stop:
                    t.stop = trail
                    t.trail_armed = True
        return None
