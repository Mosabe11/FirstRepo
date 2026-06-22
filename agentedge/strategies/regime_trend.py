"""
strategies/regime_trend.py
--------------------------
Regime-filtered trend / breakout strategy — the one configuration that showed a
cost-surviving out-of-sample edge in research/ (daily timeframe, PF ~1.2 OOS
with fees). It only fires inside a confirmed trend and stands aside in chop,
which is the whole reason the intraday momentum strategies bled: they traded
the chop.

Entry (LONG):  ADX >= adx_min  AND  close > EMA(trend)  AND  EMA(fast) > EMA(trend)
               AND  close breaks the prior `donchian`-bar high.
SHORT: the mirror.

Risk geometry is handed to the position manager: stop = entry ∓ 1.5*ATR,
target = 2.5R. Designed for the 1d timeframe; the live edge was NOT present
on 1h/4h once costs were modeled, so do not drop the timeframe without
re-validating in research/.

IMPORTANT: validated on a small OOS sample (~20-60 trades across BTC/ETH/SOL).
Treat as PAPER-ONLY until the self-learning layer accumulates a statistically
meaningful live record. See research/ for how to re-run the validation.
"""

from __future__ import annotations
import numpy as np

from core.signal import Signal
from core.indicators import ema, atr, adx
from strategies.base import Strategy


class RegimeTrendStrategy(Strategy):
    name = "regime_trend"
    timeframe = "1d"
    min_candles = 90

    ADX_MIN = 22.0
    DONCHIAN = 20
    EMA_TREND = 50
    EMA_FAST = 21
    STOP_ATR_MULT = 1.5
    TARGET_R = 2.5

    def evaluate(self, asset: str, candles: list[list]) -> Signal:
        if len(candles) < self.min_candles:
            return self._flat(asset, 0, self.name, self.timeframe, "not enough candles")

        arr = np.array(candles, dtype=float)
        high, low, close = arr[:, 2], arr[:, 3], arr[:, 4]
        price = float(close[-1])

        ef = ema(close, self.EMA_FAST)
        es = ema(close, self.EMA_TREND)
        adx_arr = adx(high, low, close, 14)
        atr_arr = atr(high, low, close, 14)

        if not (self._valid_last(ef, 1) and self._valid_last(es, 1) and
                self._valid_last(adx_arr, 1) and self._valid_last(atr_arr, 1)):
            return self._flat(asset, price, self.name, self.timeframe,
                              "indicators not warmed up")

        atr_val = float(atr_arr[-1])
        adx_val = float(adx_arr[-1])
        if atr_val <= 0:
            return self._flat(asset, price, self.name, self.timeframe, "zero ATR")

        # prior-window Donchian (exclude the current bar so it isn't self-referential)
        donch_high = float(np.max(high[-self.DONCHIAN - 1:-1]))
        donch_low = float(np.min(low[-self.DONCHIAN - 1:-1]))
        trending = adx_val >= self.ADX_MIN

        long_setup = (trending and price > es[-1] and ef[-1] > es[-1]
                      and price >= donch_high)
        short_setup = (trending and price < es[-1] and ef[-1] < es[-1]
                       and price <= donch_low)

        if long_setup:
            stop = price - self.STOP_ATR_MULT * atr_val
            risk = price - stop
            tp = price + self.TARGET_R * risk
            return Signal(
                asset=asset, direction="LONG", edge=self._score(adx_val),
                price=price, strategy=self.name, timeframe=self.timeframe,
                reason=f"trend-up breakout (ADX {adx_val:.0f}, >{self.DONCHIAN}d high)",
                stop_loss=stop, take_profit=tp,
                extras={"adx": adx_val, "atr": atr_val},
            )
        if short_setup:
            stop = price + self.STOP_ATR_MULT * atr_val
            risk = stop - price
            tp = price - self.TARGET_R * risk
            return Signal(
                asset=asset, direction="SHORT", edge=self._score(adx_val),
                price=price, strategy=self.name, timeframe=self.timeframe,
                reason=f"trend-down breakout (ADX {adx_val:.0f}, <{self.DONCHIAN}d low)",
                stop_loss=stop, take_profit=tp,
                extras={"adx": adx_val, "atr": atr_val},
            )

        return self._flat(asset, price, self.name, self.timeframe,
                          f"no trend setup (ADX {adx_val:.0f})")

    def _score(self, adx_val: float) -> float:
        """Edge 60-90, scaled by trend strength. ADX 22 -> 60, ADX 50+ -> 90."""
        return float(min(90.0, 60.0 + (adx_val - self.ADX_MIN) * 1.1))
