"""
strategies/__init__.py
----------------------
Registry of strategies, instantiated from settings flags.
Bots import `scalping_strategies` and `swing_strategies`.
"""

from config import settings
from strategies.base import Strategy
from strategies.swing_1h import Swing1hStrategy
from strategies.vwap_macd import VwapMacdStrategy
from strategies.keltner_rsi import KeltnerRsiStrategy
from strategies.alma_stoch import AlmaStochStrategy
from strategies.rsi_bb_revert import RsiBbRevertStrategy
from strategies.ema_cross import EmaCrossStrategy
from strategies.regime_trend import RegimeTrendStrategy


def build_scalping_strategies() -> list[Strategy]:
    """فصل الستراتيجيات حسب الفئة (بناءً على تحليل 866 صفقة)."""
    out: list[Strategy] = []
    if settings.ENABLE_VWAP_MACD:
        s = VwapMacdStrategy(); s.allowed_classes = {"binance"}; out.append(s)
    if settings.ENABLE_KELTNER_RSI:
        s = KeltnerRsiStrategy(); s.allowed_classes = {"crypto", "binance"}; out.append(s)
    if settings.ENABLE_ALMA_STOCH:
        s = AlmaStochStrategy(); s.allowed_classes = {"crypto", "binance"}; out.append(s)
    if settings.ENABLE_RSI_BB_REVERT:
        s = RsiBbRevertStrategy(); s.allowed_classes = {"forex", "metal"}; out.append(s)
    # ema_cross معطّلة نهائياً (كانت -125 على forex)
    return out


def build_swing_strategies() -> list[Strategy]:
    out: list[Strategy] = []
    if settings.ENABLE_SWING_1H:
        out.append(Swing1hStrategy())
    if getattr(settings, "ENABLE_REGIME_TREND", True):
        # The validated daily trend strategy. The scanner fetches candles at
        # each strategy's own .timeframe ("1d" here), so it gets daily bars.
        out.append(RegimeTrendStrategy())
    return out


scalping_strategies = build_scalping_strategies()
swing_strategies = build_swing_strategies()
