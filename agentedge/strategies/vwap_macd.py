"""
strategies/vwap_macd.py
-----------------------
PDF Strategy #1: VWAP and MACD Momentum.

LONG when:
  - Price closes above VWAP
  - MACD signal line crosses above MACD line  (the PDF's wording — we treat
    this as the standard bullish MACD cross: macd_line > signal_line where
    previous bar had macd_line <= signal_line)
  - MACD histogram turns positive
SHORT: mirror.

Stop loss: recent swing low / high.
Take profit: opposite MACD crossover, or 1.5:1 R:R — we set the static TP
at 1.5x risk distance, monitor handles the dynamic exit.

Designed for 5m scalping (works on 1m too, but with more noise).
"""

from __future__ import annotations
import numpy as np

from core.signal import Signal
from core.indicators import vwap, macd
from strategies.base import Strategy


class VwapMacdStrategy(Strategy):
    name = "vwap_macd"
    timeframe = "5m"
    min_candles = 60

    SWING_LOOKBACK = 10
    RR_RATIO = 2.5

    def evaluate(self, asset: str, candles: list[list]) -> Signal:
        if len(candles) < self.min_candles:
            return self._flat(asset, 0, self.name, self.timeframe,
                              "not enough candles")

        arr = np.array(candles, dtype=float)
        high, low, close, vol = arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5]
        price = float(close[-1])

        vwap_arr = vwap(high, low, close, vol)
        macd_line, signal_line, hist = macd(close)

        if not (self._valid_last(vwap_arr, 2) and
                self._valid_last(macd_line, 2) and
                self._valid_last(signal_line, 2) and
                self._valid_last(hist, 1)):
            return self._flat(asset, price, self.name, self.timeframe,
                              "indicators not warmed up")

        above_vwap = close[-1] > vwap_arr[-1]
        below_vwap = close[-1] < vwap_arr[-1]

        # bullish cross: macd_line crossed above signal_line on this bar
        bull_cross = (macd_line[-1] > signal_line[-1]) and \
                     (macd_line[-2] <= signal_line[-2])
        bear_cross = (macd_line[-1] < signal_line[-1]) and \
                     (macd_line[-2] >= signal_line[-2])
        hist_positive = hist[-1] > 0
        hist_negative = hist[-1] < 0

        swing_low = float(np.min(low[-self.SWING_LOOKBACK:]))
        swing_high = float(np.max(high[-self.SWING_LOOKBACK:]))

        if above_vwap and bull_cross and hist_positive:
            sl = swing_low
            risk = price - sl
            if risk <= 0:
                return self._flat(asset, price, self.name, self.timeframe,
                                  "invalid risk distance")
            tp = price + risk * self.RR_RATIO
            edge = self._score(price, vwap_arr[-1], hist, "long")
            return Signal(
                asset=asset, direction="LONG", edge=edge, price=price,
                strategy=self.name, timeframe=self.timeframe,
                reason=f"price above VWAP, MACD bull cross, hist+",
                stop_loss=sl, take_profit=tp,
                extras={"vwap": float(vwap_arr[-1]),
                        "macd_hist": float(hist[-1])},
            )

        if below_vwap and bear_cross and hist_negative:
            sl = swing_high
            risk = sl - price
            if risk <= 0:
                return self._flat(asset, price, self.name, self.timeframe,
                                  "invalid risk distance")
            tp = price - risk * self.RR_RATIO
            edge = self._score(price, vwap_arr[-1], hist, "short")
            return Signal(
                asset=asset, direction="SHORT", edge=edge, price=price,
                strategy=self.name, timeframe=self.timeframe,
                reason=f"price below VWAP, MACD bear cross, hist-",
                stop_loss=sl, take_profit=tp,
                extras={"vwap": float(vwap_arr[-1]),
                        "macd_hist": float(hist[-1])},
            )

        return self._flat(asset, price, self.name, self.timeframe,
                          "no VWAP/MACD setup")

    def _score(self, price: float, vwap_val: float,
               hist: np.ndarray, side: str) -> float:
        """
        Edge scoring 0-100. Base 60 for any valid setup, add for:
          - distance from VWAP (further = stronger momentum, capped)
          - histogram magnitude growing
        """
        edge = 60.0
        vwap_dist_pct = abs(price - vwap_val) / max(1e-9, vwap_val) * 100
        edge += min(20, vwap_dist_pct * 5)  # up to +20

        if len(hist) >= 3 and np.isfinite(hist[-3]):
            growth = abs(hist[-1]) - abs(hist[-3])
            if (side == "long" and growth > 0) or (side == "short" and growth > 0):
                edge += 10
        return min(95, edge)
