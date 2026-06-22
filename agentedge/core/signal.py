"""
core/signal.py
--------------
Shared Signal dataclass returned by every strategy.
"""

from dataclasses import dataclass, field
from typing import Literal
import time


@dataclass
class Signal:
    asset: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    edge: float                # 0-100 confidence score
    price: float               # price observed at signal time
    strategy: str              # which strategy produced this
    timeframe: str             # "1m" / "5m" / "1h"
    reason: str = ""           # human-readable explanation
    stop_loss: float | None = None
    take_profit: float | None = None
    timestamp: float = field(default_factory=time.time)
    extras: dict = field(default_factory=dict)  # indicator values for logging

    @property
    def is_actionable(self) -> bool:
        return self.direction != "FLAT" and self.edge > 0

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "direction": self.direction,
            "edge": round(self.edge, 1),
            "price": self.price,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "reason": self.reason,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "timestamp": self.timestamp,
        }
