"""
strategies/swing_1h.py
----------------------
Port of the v10 multi-factor swing scorer onto the new Strategy interface.

Combines EMA trend alignment, RSI momentum, MACD histogram, Bollinger
position, and volume confirmation into a single 0-100 edge score for
LONG and SHORT separately. Whichever side scores higher AND clears the
threshold wins.

Runs on 1h timeframe — used by the slow auto_scanner bot, not the
high-frequency trigger.
"""

from __future__ import annotations
import numpy as np

from core.signal import Signal
from core.indicators import compute_all
from strategies.base import Strategy


class Swing1hStrategy(Strategy):
    name = "swing_1h"
    timeframe = "1h"
    min_candles = 60

    def evaluate(self, asset: str, candles: list[list]) -> Signal:
        if len(candles) < self.min_candles:
            return self._flat(asset, 0, self.name, self.timeframe,
                              "not enough candles")

        ind = compute_all(candles)
        if not ind:
            return self._flat(asset, 0, self.name, self.timeframe,
                              "indicator compute failed")

        close = ind["close"]
        price = float(close[-1])
        ema9, ema21, ema50 = ind["ema9"], ind["ema21"], ind["ema50"]
        rsi_v = ind["rsi"]
        macd_h = ind["macd_hist"]
        bb_up, bb_mid, bb_low = ind["bb_upper"], ind["bb_middle"], ind["bb_lower"]
        vol, vol_sma = ind["volume"], ind["vol_sma"]
        atr_v = ind["atr"]

        # bail if anything critical is NaN at the tail
        for arr in (ema9, ema21, ema50, rsi_v, macd_h, bb_mid, atr_v, vol_sma):
            if not self._valid_last(arr, 1):
                return self._flat(asset, price, self.name, self.timeframe,
                                  "indicators not ready")

        # ----- LONG score -----
        long_score = 0.0
        # trend alignment
        if ema9[-1] > ema21[-1]:
            long_score += 18
        if ema21[-1] > ema50[-1]:
            long_score += 12
        if close[-1] > ema50[-1]:
            long_score += 8
        # momentum
        if 50 < rsi_v[-1] < 70:
            long_score += 15
        elif rsi_v[-1] >= 70:
            long_score += 5  # overbought — less ideal
        if macd_h[-1] > 0:
            long_score += 12
        if len(macd_h) >= 3 and macd_h[-1] > macd_h[-3]:
            long_score += 6
        # pullback into BB middle (good entry zone)
        if abs(close[-1] - bb_mid[-1]) / max(1e-9, bb_mid[-1]) < 0.005:
            long_score += 8
        # volume
        vol_ratio = vol[-1] / max(1e-9, vol_sma[-1])
        if vol_ratio > 1.2:
            long_score += 8
        elif vol_ratio > 0.8:
            long_score += 4
        # volatility sanity — don't trade dead markets
        atr_pct = atr_v[-1] / max(1e-9, close[-1])
        if 0.003 < atr_pct < 0.05:
            long_score += 6

        # ----- SHORT score (mirror) -----
        short_score = 0.0
        if ema9[-1] < ema21[-1]:
            short_score += 18
        if ema21[-1] < ema50[-1]:
            short_score += 12
        if close[-1] < ema50[-1]:
            short_score += 8
        if 30 < rsi_v[-1] < 50:
            short_score += 15
        elif rsi_v[-1] <= 30:
            short_score += 5
        if macd_h[-1] < 0:
            short_score += 12
        if len(macd_h) >= 3 and macd_h[-1] < macd_h[-3]:
            short_score += 6
        if abs(close[-1] - bb_mid[-1]) / max(1e-9, bb_mid[-1]) < 0.005:
            short_score += 8
        if vol_ratio > 1.2:
            short_score += 8
        elif vol_ratio > 0.8:
            short_score += 4
        if 0.003 < atr_pct < 0.05:
            short_score += 6

        # pick winner
        if long_score >= short_score:
            direction = "LONG"
            edge = long_score
            atr_dist = float(atr_v[-1])
            sl = price - atr_dist * 1.5
            tp = price + atr_dist * 2.5
        else:
            direction = "SHORT"
            edge = short_score
            atr_dist = float(atr_v[-1])
            sl = price + atr_dist * 1.5
            tp = price - atr_dist * 2.5

        if edge < 30:
            return self._flat(asset, price, self.name, self.timeframe,
                              f"edge too low ({edge:.0f})")

        return Signal(
            asset=asset, direction=direction, edge=float(edge),
            price=price, strategy=self.name, timeframe=self.timeframe,
            reason=f"swing scorer edge {edge:.0f}",
            stop_loss=sl, take_profit=tp,
            extras={
                "rsi": float(rsi_v[-1]),
                "macd_hist": float(macd_h[-1]),
                "ema9": float(ema9[-1]),
                "ema21": float(ema21[-1]),
                "vol_ratio": float(vol_ratio),
            },
        )
