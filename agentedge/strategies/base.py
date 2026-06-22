"""
strategies/base.py
------------------
Abstract Strategy interface. All strategies (legacy swing + new scalping)
implement the same `evaluate(asset, candles) -> Signal` contract so the
bots can treat them uniformly.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
import numpy as np

from core.signal import Signal


class Strategy(ABC):
    name: str = "base"
    timeframe: str = "1h"  # "1m" / "5m" / "1h"
    min_candles: int = 50  # how many bars needed before evaluation

    @abstractmethod
    def evaluate(self, asset: str, candles: list[list]) -> Signal:
        """
        candles: list of [ts, open, high, low, close, volume].
        Returns a Signal (may be FLAT with edge=0 to mean no setup).
        """
        ...

    @staticmethod
    def _flat(asset: str, price: float, strategy: str,
              timeframe: str, reason: str = "") -> Signal:
        return Signal(
            asset=asset, direction="FLAT", edge=0.0,
            price=price, strategy=strategy, timeframe=timeframe,
            reason=reason,
        )

    @staticmethod
    def _valid_last(arr, k: int = 1) -> bool:
        """Check that the last k values of an indicator array are finite."""
        if arr is None or len(arr) < k:
            return False
        tail = arr[-k:]
        return bool(np.all(np.isfinite(tail)))
