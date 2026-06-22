"""
strategies/alma_stoch.py
------------------------
PDF Strategy #3: ALMA and Stochastic Oscillator.

Bullish (LONG):
  - Price closes above ALMA  (trend filter)
  - Stochastic Oscillator (%K) crosses above 20  (momentum)
Bearish (SHORT): mirror with ALMA below + Stochastic cross below 80.

Stop loss: nearest swing low / high.
Take profit (exit signal): Stochastic > 80 (long) or < 20 (short) —
  we set the static TP using R:R = 1.5, monitor handles dynamic exit.

Designed for 1m scalping per the doc.
"""

from __future__ import annotations
import numpy as np

from core.signal import Signal
from core.indicators import alma, stochastic
from strategies.base import Strategy


class AlmaStochStrategy(Strategy):
    name = "alma_stoch"
    timeframe = "1m"
    min_candles = 40

    SWING_LOOKBACK = 8
    RR_RATIO = 2.5

    def evaluate(self, asset: str, candles: list[list]) -> Signal:
        if len(candles) < self.min_candles:
            return self._flat(asset, 0, self.name, self.timeframe,
                              "not enough candles")

        arr = np.array(candles, dtype=float)
        high, low, close = arr[:, 2], arr[:, 3], arr[:, 4]
        price = float(close[-1])

        alma_arr = alma(close, period=9, offset=0.85, sigma=6.0)
        k, d = stochastic(high, low, close, k_period=14, d_period=3)

        if not (self._valid_last(alma_arr, 2) and self._valid_last(k, 2)):
            return self._flat(asset, price, self.name, self.timeframe,
                              "indicators not warmed up")

        above_alma = close[-1] > alma_arr[-1]
        below_alma = close[-1] < alma_arr[-1]

        # Stochastic %K crossing thresholds
        k_cross_above_20 = k[-1] > 20 and k[-2] <= 20
        k_cross_below_80 = k[-1] < 80 and k[-2] >= 80

        swing_low = float(np.min(low[-self.SWING_LOOKBACK:]))
        swing_high = float(np.max(high[-self.SWING_LOOKBACK:]))

        if above_alma and k_cross_above_20:
            sl = swing_low
            risk = price - sl
            if risk <= 0:
                return self._flat(asset, price, self.name, self.timeframe,
                                  "invalid risk distance")
            tp = price + risk * self.RR_RATIO
            edge = self._score(k, alma_arr, close, side="long")
            return Signal(
                asset=asset, direction="LONG", edge=edge, price=price,
                strategy=self.name, timeframe=self.timeframe,
                reason="above ALMA + Stoch%K cross above 20",
                stop_loss=sl, take_profit=tp,
                extras={"alma": float(alma_arr[-1]),
                        "stoch_k": float(k[-1])},
            )

        if below_alma and k_cross_below_80:
            sl = swing_high
            risk = sl - price
            if risk <= 0:
                return self._flat(asset, price, self.name, self.timeframe,
                                  "invalid risk distance")
            tp = price - risk * self.RR_RATIO
            edge = self._score(k, alma_arr, close, side="short")
            return Signal(
                asset=asset, direction="SHORT", edge=edge, price=price,
                strategy=self.name, timeframe=self.timeframe,
                reason="below ALMA + Stoch%K cross below 80",
                stop_loss=sl, take_profit=tp,
                extras={"alma": float(alma_arr[-1]),
                        "stoch_k": float(k[-1])},
            )

        return self._flat(asset, price, self.name, self.timeframe,
                          "no ALMA/Stoch setup")

    def _score(self, k: np.ndarray, alma_arr: np.ndarray,
               close: np.ndarray, side: str) -> float:
        edge = 65.0  # cleaner trigger condition than the others
        # ALMA slope = trend strength
        if len(alma_arr) >= 5 and np.all(np.isfinite(alma_arr[-5:])):
            slope = alma_arr[-1] - alma_arr[-5]
            if side == "long" and slope > 0:
                edge += min(15, abs(slope / alma_arr[-1]) * 5000)
            elif side == "short" and slope < 0:
                edge += min(15, abs(slope / alma_arr[-1]) * 5000)
        return min(95, edge)
