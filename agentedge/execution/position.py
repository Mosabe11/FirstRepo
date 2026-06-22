"""
execution/position.py
---------------------
Position dataclass + the state machine for stop / target / trailing.

v3 (validated): R-MULTIPLE management. "R" = the initial risk = |entry - stop|
at entry. Everything is measured in R, not raw percent, so management adapts to
each asset's volatility instead of strangling winners with fixed +1% rules.

  - Target sits at TARGET_R (default 2.5R).
  - Break-even arms only after +BE_R (1.0R): stop -> entry + 0.1R (covers fees),
    NOT entry+0.2% the moment price breathes. This is the fix for the inverted
    R:R the live logs and the backtest both showed.
  - A wide trail (TRAIL_DIST_R, ~1.3R off the high-water mark) engages only after
    +TRAIL_R (1.5R), so winners are allowed to actually reach the target.

The Monitor bot calls `update(price)` every few seconds. It returns:
  "HOLD"  — keep open
  "TP"    — target hit
  "SL"    — stop hit (a loss)
  "TRAIL" — trailing stop hit after the trade was already in profit (a win/scratch)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import time
import uuid

# ---- management constants (in R) ----
BE_R = 1.0          # arm break-even after +1.0R
TRAIL_R = 1.5       # start trailing after +1.5R
TRAIL_DIST_R = 1.3  # trail this many R behind the high-water mark
BE_LOCK_R = 0.1     # where break-even parks the stop (entry + 0.1R) to cover costs


@dataclass
class Position:
    id: str
    asset: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    strategy: str
    initial_stop: float = 0.0       # frozen at entry -> defines 1R
    entry_fee: float = 0.0          # fee paid on entry, charged back at close
    opened_at: float = field(default_factory=time.time)
    breakeven_armed: bool = False
    trailing_armed: bool = False
    high_water_mark: float = 0.0    # best favorable price seen so far
    low_water_mark: float = 0.0
    original_quantity: float = 0.0
    paper: bool = True

    @classmethod
    def new(cls, asset: str, direction: str, entry: float, qty: float,
            sl: float, tp: float, strategy: str, paper: bool,
            entry_fee: float = 0.0) -> "Position":
        return cls(
            id=uuid.uuid4().hex[:10],
            asset=asset, direction=direction, entry_price=entry,
            quantity=qty, stop_loss=sl, take_profit=tp,
            strategy=strategy,
            initial_stop=sl, entry_fee=entry_fee,
            high_water_mark=entry, low_water_mark=entry,
            original_quantity=qty,
            paper=paper,
        )

    # ---- risk geometry ----
    @property
    def risk_per_unit(self) -> float:
        """1R in price terms, frozen at entry."""
        r = abs(self.entry_price - self.initial_stop)
        return r if r > 0 else max(self.entry_price * 1e-6, 1e-9)

    def r_multiple(self, price: float) -> float:
        move = ((price - self.entry_price) if self.direction == "LONG"
                else (self.entry_price - price))
        return move / self.risk_per_unit

    def pnl(self, current_price: float) -> float:
        if self.direction == "LONG":
            return (current_price - self.entry_price) * self.quantity
        return (self.entry_price - current_price) * self.quantity

    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.direction == "LONG":
            return (current_price - self.entry_price) / self.entry_price
        return (self.entry_price - current_price) / self.entry_price

    def update(self, price: float) -> str:
        """Returns "HOLD" | "TP" | "SL" | "TRAIL". Side-effects: arms break-even
        / advances the trailing stop. R-multiple based — see module docstring."""
        risk = self.risk_per_unit
        r = self.r_multiple(price)

        if self.direction == "LONG":
            self.high_water_mark = max(self.high_water_mark, price)
            if price >= self.take_profit:
                return "TP"
            if price <= self.stop_loss:
                return "TRAIL" if self.trailing_armed else "SL"
            if not self.breakeven_armed and r >= BE_R:
                self.stop_loss = max(self.stop_loss, self.entry_price + BE_LOCK_R * risk)
                self.breakeven_armed = True
            if r >= TRAIL_R:
                trail = self.high_water_mark - TRAIL_DIST_R * risk
                if trail > self.stop_loss:
                    self.stop_loss = trail
                    self.trailing_armed = True
            return "HOLD"

        else:  # SHORT
            self.low_water_mark = (min(self.low_water_mark, price)
                                   if self.low_water_mark else price)
            if price <= self.take_profit:
                return "TP"
            if price >= self.stop_loss:
                return "TRAIL" if self.trailing_armed else "SL"
            if not self.breakeven_armed and r >= BE_R:
                self.stop_loss = min(self.stop_loss, self.entry_price - BE_LOCK_R * risk)
                self.breakeven_armed = True
            if r >= TRAIL_R:
                trail = self.low_water_mark + TRAIL_DIST_R * risk
                if trail < self.stop_loss:
                    self.stop_loss = trail
                    self.trailing_armed = True
            return "HOLD"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "asset": self.asset,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "strategy": self.strategy,
            "initial_stop": self.initial_stop,
            "entry_fee": self.entry_fee,
            "opened_at": self.opened_at,
            "breakeven_armed": self.breakeven_armed,
            "trailing_armed": self.trailing_armed,
            "high_water_mark": self.high_water_mark,
            "low_water_mark": self.low_water_mark,
            "original_quantity": self.original_quantity,
            "paper": self.paper,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        entry = d["entry_price"]
        return cls(
            id=d["id"], asset=d["asset"], direction=d["direction"],
            entry_price=entry, quantity=d["quantity"],
            stop_loss=d["stop_loss"], take_profit=d["take_profit"],
            strategy=d["strategy"],
            initial_stop=d.get("initial_stop", d["stop_loss"]),
            entry_fee=d.get("entry_fee", 0.0),
            opened_at=d.get("opened_at", time.time()),
            breakeven_armed=d.get("breakeven_armed", False),
            trailing_armed=d.get("trailing_armed", False),
            # restore water marks if present, else seed at entry (no reset bug)
            high_water_mark=d.get("high_water_mark", entry),
            low_water_mark=d.get("low_water_mark", entry),
            original_quantity=d.get("original_quantity", d["quantity"]),
            paper=d.get("paper", True),
        )
