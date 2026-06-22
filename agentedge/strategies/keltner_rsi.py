"""
strategies/keltner_rsi.py
-------------------------
PDF Strategy #2: Keltner Channels and RSI Trend Reversal.

LONG when:
  - Two consecutive closes below lower Keltner Channel
  - RSI breaks above 50
SHORT: mirror with upper Keltner + RSI < 50.

Stop loss: opposite Keltner boundary.
Take profit: channel midpoint (EMA20), or RSI > 70 / < 30 reached.

Mean-reversion oriented — best on 5m timeframe.
"""

from __future__ import annotations
import numpy as np

from core.signal import Signal
from core.indicators import keltner_channels, rsi
from strategies.base import Strategy


class KeltnerRsiStrategy(Strategy):
    name = "keltner_rsi"
    timeframe = "5m"
    min_candles = 60

    def evaluate(self, asset: str, candles: list[list]) -> Signal:
        if len(candles) < self.min_candles:
            return self._flat(asset, 0, self.name, self.timeframe,
                              "not enough candles")

        arr = np.array(candles, dtype=float)
        high, low, close = arr[:, 2], arr[:, 3], arr[:, 4]
        price = float(close[-1])

        kc_up, kc_mid, kc_low = keltner_channels(high, low, close)
        rsi_arr = rsi(close, 14)

        if not (self._valid_last(kc_up, 2) and self._valid_last(kc_low, 2)
                and self._valid_last(rsi_arr, 2)):
            return self._flat(asset, price, self.name, self.timeframe,
                              "indicators not warmed up")

        # consecutive closes below lower / above upper
        two_below = close[-1] < kc_low[-1] and close[-2] < kc_low[-2]
        two_above = close[-1] > kc_up[-1] and close[-2] > kc_up[-2]

        rsi_up_cross_50 = rsi_arr[-1] > 50 and rsi_arr[-2] <= 50
        rsi_dn_cross_50 = rsi_arr[-1] < 50 and rsi_arr[-2] >= 50

        # For mean-reversion, the trigger combines:
        # an overextended condition (we saw two closes outside channel),
        # then a momentum-shift confirmation (RSI cross of 50)
        if two_below and rsi_arr[-1] > 50:
            # SL ضيق، TP بعيد للحصول على R:R أفضل
            sl = float(kc_low[-1]) * 0.998  # تحت الـ lower بشوي
            risk = price - sl
            tp = price + risk * 2.5
            edge = self._score(rsi_arr, rsi_up_cross_50, side="long")
            return Signal(
                asset=asset, direction="LONG", edge=edge, price=price,
                strategy=self.name, timeframe=self.timeframe,
                reason="2 closes below lower KC + RSI > 50",
                stop_loss=sl, take_profit=tp,
                extras={"rsi": float(rsi_arr[-1]),
                        "kc_low": float(kc_low[-1]),
                        "kc_up": float(kc_up[-1])},
            )

        if two_above and rsi_arr[-1] < 50:
            sl = float(kc_up[-1]) * 1.002  # فوق الـ upper بشوي
            risk = sl - price
            tp = price - risk * 2.5
            edge = self._score(rsi_arr, rsi_dn_cross_50, side="short")
            return Signal(
                asset=asset, direction="SHORT", edge=edge, price=price,
                strategy=self.name, timeframe=self.timeframe,
                reason="2 closes above upper KC + RSI < 50",
                stop_loss=sl, take_profit=tp,
                extras={"rsi": float(rsi_arr[-1]),
                        "kc_low": float(kc_low[-1]),
                        "kc_up": float(kc_up[-1])},
            )

        return self._flat(asset, price, self.name, self.timeframe,
                          "no Keltner/RSI setup")

    def _score(self, rsi_arr: np.ndarray, fresh_cross: bool, side: str) -> float:
        edge = 60.0
        # Fresh 50-cross is highest-quality entry
        if fresh_cross:
            edge += 15
        # Stronger RSI reading after cross adds more confidence
        latest = rsi_arr[-1]
        if side == "long":
            edge += min(15, max(0, latest - 50) * 0.6)
        else:
            edge += min(15, max(0, 50 - latest) * 0.6)
        return min(95, edge)
