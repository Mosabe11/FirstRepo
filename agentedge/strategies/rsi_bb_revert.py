"""
strategies/rsi_bb_revert.py
---------------------------
PDF Strategy #4: RSI + Bollinger Bands Mean Reversion.

LONG entry:
  - Price touches or breaches the lower Bollinger Band
  - RSI crosses above 30 from below (momentum shifting back up)
SHORT entry: mirror with upper band + RSI crossing below 70.

Stop loss: just outside the Bollinger Band or swing low/high.
Take profit: opposite Bollinger Band touch (middle band = first target).

Classic mean-reversion, works 1m / 5m.
"""

from __future__ import annotations
import numpy as np

from core.signal import Signal
from core.indicators import bollinger_bands, rsi
from strategies.base import Strategy


class RsiBbRevertStrategy(Strategy):
    name = "rsi_bb_revert"
    timeframe = "5m"
    min_candles = 40

    SL_BAND_BUFFER = 0.0015  # 0.15% beyond the band for stop placement

    def evaluate(self, asset: str, candles: list[list]) -> Signal:
        if len(candles) < self.min_candles:
            return self._flat(asset, 0, self.name, self.timeframe,
                              "not enough candles")

        arr = np.array(candles, dtype=float)
        high, low, close = arr[:, 2], arr[:, 3], arr[:, 4]
        price = float(close[-1])

        bb_up, bb_mid, bb_low = bollinger_bands(close, 20, 2.0)
        rsi_arr = rsi(close, 14)

        if not (self._valid_last(bb_up, 2) and self._valid_last(bb_low, 2)
                and self._valid_last(rsi_arr, 2)):
            return self._flat(asset, price, self.name, self.timeframe,
                              "indicators not warmed up")

        # "Touches or breaches": current low <= lower band OR current
        # close <= lower band on this bar.
        touched_lower = low[-1] <= bb_low[-1] or close[-1] <= bb_low[-1]
        touched_upper = high[-1] >= bb_up[-1] or close[-1] >= bb_up[-1]

        rsi_up_cross_30 = rsi_arr[-1] > 30 and rsi_arr[-2] <= 30
        rsi_dn_cross_70 = rsi_arr[-1] < 70 and rsi_arr[-2] >= 70

        if touched_lower and rsi_up_cross_30:
            sl = float(bb_low[-1]) * (1 - self.SL_BAND_BUFFER)
            # also respect swing low if lower
            swing_low = float(low[-8:].min())
            sl = min(sl, swing_low)
            tp = float(bb_up[-1])  # opposite band
            edge = self._score(rsi_arr, bb_low, close, side="long")
            return Signal(
                asset=asset, direction="LONG", edge=edge, price=price,
                strategy=self.name, timeframe=self.timeframe,
                reason="lower BB touched + RSI cross above 30",
                stop_loss=sl, take_profit=tp,
                extras={"rsi": float(rsi_arr[-1]),
                        "bb_low": float(bb_low[-1]),
                        "bb_up": float(bb_up[-1])},
            )

        if touched_upper and rsi_dn_cross_70:
            sl = float(bb_up[-1]) * (1 + self.SL_BAND_BUFFER)
            swing_high = float(high[-8:].max())
            sl = max(sl, swing_high)
            tp = float(bb_low[-1])
            edge = self._score(rsi_arr, bb_up, close, side="short")
            return Signal(
                asset=asset, direction="SHORT", edge=edge, price=price,
                strategy=self.name, timeframe=self.timeframe,
                reason="upper BB touched + RSI cross below 70",
                stop_loss=sl, take_profit=tp,
                extras={"rsi": float(rsi_arr[-1]),
                        "bb_low": float(bb_low[-1]),
                        "bb_up": float(bb_up[-1])},
            )

        return self._flat(asset, price, self.name, self.timeframe,
                          "no RSI/BB reversion setup")

    def _score(self, rsi_arr: np.ndarray, band: np.ndarray,
               close: np.ndarray, side: str) -> float:
        edge = 60.0
        # deeper RSI overshoot = stronger reversion potential
        latest = rsi_arr[-1]
        if side == "long":
            # The lower the bar before crossing back, the better
            # Look at minimum RSI in last 5 bars
            recent_min = np.nanmin(rsi_arr[-5:])
            edge += min(20, max(0, 30 - recent_min) * 1.5)
        else:
            recent_max = np.nanmax(rsi_arr[-5:])
            edge += min(20, max(0, recent_max - 70) * 1.5)

        # price truly outside the band (not just touching) = stronger
        if side == "long" and close[-1] < band[-1]:
            edge += 8
        elif side == "short" and close[-1] > band[-1]:
            edge += 8
        return min(95, edge)
