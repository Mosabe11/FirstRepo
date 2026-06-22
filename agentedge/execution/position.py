"""
execution/position.py
---------------------
Position dataclass + the state machine for TP/SL/breakeven/trailing.

The Monitor bot calls `update(price)` every few seconds. It returns one of:
  "HOLD"     — keep the position open
  "TP"       — take profit hit
  "SL"       — stop loss hit
  "TRAIL"    — trailing stop hit (counts as a profitable close)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import time
import uuid

from config import settings


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
    opened_at: float = field(default_factory=time.time)
    breakeven_armed: bool = False
    trailing_armed: bool = False
    half_closed: bool = False        # ربع: نصف الصفقة اتسكر
    tight_trail_armed: bool = False  # خامسا: trail ضيق بعد +2%
    high_water_mark: float = 0.0  # best favorable price seen so far
    low_water_mark: float = 0.0
    original_quantity: float = 0.0   # الكمية الأصلية قبل الـ partial close
    paper: bool = True

    @classmethod
    def new(cls, asset: str, direction: str, entry: float, qty: float,
            sl: float, tp: float, strategy: str, paper: bool) -> "Position":
        return cls(
            id=uuid.uuid4().hex[:10],
            asset=asset, direction=direction, entry_price=entry,
            quantity=qty, stop_loss=sl, take_profit=tp,
            strategy=strategy,
            high_water_mark=entry, low_water_mark=entry,
            original_quantity=qty,
            paper=paper,
        )

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
        """
        Returns "HOLD" | "TP" | "SL" | "TRAIL".
        Side-effects: arms breakeven / updates trailing stop.
        """
        if self.direction == "LONG":
            self.high_water_mark = max(self.high_water_mark, price)
            pct = self.pnl_pct(price)
            # 1) Take profit hit
            if price >= self.take_profit:
                return "TP"
            # 2) Stop loss hit
            if price <= self.stop_loss:
                return "TRAIL" if self.trailing_armed else "SL"
            # 3) المرحلة الأولى: +0.5% → SL ينقل لـ Entry + 0.2% (حفظ ربح صغير)
            if not self.breakeven_armed and pct >= 0.010:
                self.stop_loss = self.entry_price * 1.002
                self.breakeven_armed = True
            # 4) المرحلة الثانية: +1.0% → اقفل 50% (HALF) + SL لـ Entry + 0.5%
            if self.breakeven_armed and not self.half_closed and pct >= 0.015:
                return "HALF"
            # 5) المرحلة الثالثة: trailing بعد half-close
            if self.half_closed:
                if not self.tight_trail_armed and pct >= 0.02:
                    # +2% → trail ضيق 0.2%
                    self.tight_trail_armed = True
                trail_dist = 0.003 if self.tight_trail_armed else 0.006
                trail_sl = self.high_water_mark * (1 - trail_dist)
                if trail_sl > self.stop_loss:
                    self.stop_loss = trail_sl
                    self.trailing_armed = True
            return "HOLD"

        else:  # SHORT
            self.low_water_mark = min(self.low_water_mark, price) if self.low_water_mark else price
            pct = self.pnl_pct(price)
            if price <= self.take_profit:
                return "TP"
            if price >= self.stop_loss:
                return "TRAIL" if self.trailing_armed else "SL"
            # المرحلة الأولى: +0.5% → SL لـ Entry - 0.2%
            if not self.breakeven_armed and pct >= 0.010:
                self.stop_loss = self.entry_price * 0.998
                self.breakeven_armed = True
            # المرحلة الثانية: +1.0% → نص الصفقة
            if self.breakeven_armed and not self.half_closed and pct >= 0.015:
                return "HALF"
            # المرحلة الثالثة: trailing بعد half
            if self.half_closed:
                if not self.tight_trail_armed and pct >= 0.02:
                    self.tight_trail_armed = True
                trail_dist = 0.003 if self.tight_trail_armed else 0.006
                trail_sl = self.low_water_mark * (1 + trail_dist)
                if trail_sl < self.stop_loss:
                    self.stop_loss = trail_sl
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
            "opened_at": self.opened_at,
            "breakeven_armed": self.breakeven_armed,
            "trailing_armed": self.trailing_armed,
            "half_closed": self.half_closed,
            "tight_trail_armed": self.tight_trail_armed,
            "original_quantity": self.original_quantity,
            "paper": self.paper,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(
            id=d["id"], asset=d["asset"], direction=d["direction"],
            entry_price=d["entry_price"], quantity=d["quantity"],
            stop_loss=d["stop_loss"], take_profit=d["take_profit"],
            strategy=d["strategy"],
            opened_at=d.get("opened_at", time.time()),
            breakeven_armed=d.get("breakeven_armed", False),
            trailing_armed=d.get("trailing_armed", False),
            high_water_mark=d["entry_price"],
            low_water_mark=d["entry_price"],
            paper=d.get("paper", True),
        )
