"""
strategies/ema_cross.py — EMA Crossover Momentum (5m)

LONG: EMA9 crosses above EMA21 + EMA21 above EMA50 + Volume spike
SHORT: EMA9 crosses below EMA21 + EMA21 below EMA50 + Volume spike

أقوى في الأسواق trending، تكمّل الـ mean-reversion strategies الأخرى.
"""
import numpy as np
from core.signal import Signal
from core.indicators import ema, atr, sma
from strategies.base import Strategy


class EmaCrossStrategy(Strategy):
    name = "ema_cross"
    timeframe = "5m"
    min_candles = 60
    SWING_LOOKBACK = 10
    RR_RATIO = 2.0

    def evaluate(self, asset, candles):
        if len(candles) < self.min_candles:
            return self._flat(asset, 0, self.name, self.timeframe, "not enough candles")

        arr = np.array(candles, dtype=float)
        high, low, close, vol = arr[:,2], arr[:,3], arr[:,4], arr[:,5]
        price = float(close[-1])

        ema9  = ema(close, 9)
        ema21 = ema(close, 21)
        ema50 = ema(close, 50)
        atr_v = atr(high, low, close, 14)
        vol_sma = sma(vol, 20)

        if not (self._valid_last(ema9, 2) and self._valid_last(ema21, 2)
                and self._valid_last(ema50, 1) and self._valid_last(atr_v, 1)
                and self._valid_last(vol_sma, 1)):
            return self._flat(asset, price, self.name, self.timeframe, "not ready")

        # EMA9 cross above EMA21 (bullish)
        bull_cross = ema9[-1] > ema21[-1] and ema9[-2] <= ema21[-2]
        # EMA9 cross below EMA21 (bearish)
        bear_cross = ema9[-1] < ema21[-1] and ema9[-2] >= ema21[-2]

        # Trend filter: EMA21 vs EMA50
        uptrend   = ema21[-1] > ema50[-1]
        downtrend = ema21[-1] < ema50[-1]

        # Volume confirmation
        vol_spike = vol[-1] > vol_sma[-1] * 1.2

        swing_low  = float(np.min(low[-self.SWING_LOOKBACK:]))
        swing_high = float(np.max(high[-self.SWING_LOOKBACK:]))
        atr_val    = float(atr_v[-1])

        if bull_cross and uptrend:
            sl = max(swing_low, price - atr_val * 1.5)
            risk = price - sl
            if risk <= 0:
                return self._flat(asset, price, self.name, self.timeframe, "invalid risk")
            tp = price + risk * self.RR_RATIO
            edge = 65.0
            if vol_spike: edge += 15
            if ema21[-1] > ema50[-1] * 1.002: edge += 10  # strong trend
            return Signal(
                asset=asset, direction="LONG", edge=min(95, edge),
                price=price, strategy=self.name, timeframe=self.timeframe,
                reason=f"EMA9 cross above EMA21, uptrend confirmed{', vol spike' if vol_spike else ''}",
                stop_loss=sl, take_profit=tp,
                extras={"ema9": float(ema9[-1]), "ema21": float(ema21[-1]),
                        "ema50": float(ema50[-1])},
            )

        if bear_cross and downtrend:
            sl = min(swing_high, price + atr_val * 1.5)
            risk = sl - price
            if risk <= 0:
                return self._flat(asset, price, self.name, self.timeframe, "invalid risk")
            tp = price - risk * self.RR_RATIO
            edge = 65.0
            if vol_spike: edge += 15
            if ema21[-1] < ema50[-1] * 0.998: edge += 10
            return Signal(
                asset=asset, direction="SHORT", edge=min(95, edge),
                price=price, strategy=self.name, timeframe=self.timeframe,
                reason=f"EMA9 cross below EMA21, downtrend confirmed{', vol spike' if vol_spike else ''}",
                stop_loss=sl, take_profit=tp,
                extras={"ema9": float(ema9[-1]), "ema21": float(ema21[-1]),
                        "ema50": float(ema50[-1])},
            )

        return self._flat(asset, price, self.name, self.timeframe, "no EMA cross")
