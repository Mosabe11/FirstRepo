"""
core/indicators.py
------------------
All technical indicators used by strategies. Pure functions, numpy-based.
Inputs are typically OHLCV lists/arrays. Outputs are numpy arrays
(same length as input, with NaN for warm-up period).

The 4 scalping strategies from the research doc use:
  Strategy 1 (VWAP+MACD):       vwap, macd
  Strategy 2 (Keltner+RSI):     keltner_channels, rsi
  Strategy 3 (ALMA+Stoch):      alma, stochastic
  Strategy 4 (RSI+BB Reversion): bollinger_bands, rsi
"""

from __future__ import annotations
import numpy as np


# ---------------------------------------------------------- moving averages
def sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average."""
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out
    cumsum = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    values = np.asarray(values, dtype=float)
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    # seed with SMA of first `period` values
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def alma(values: np.ndarray, period: int = 9, offset: float = 0.85,
         sigma: float = 6.0) -> np.ndarray:
    """
    Arnaud Legoux Moving Average — low-lag MA used in scalping strategy #3.

    offset controls smoothness vs responsiveness (0.85 = balanced).
    sigma controls the Gaussian width.
    """
    values = np.asarray(values, dtype=float)
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out

    m = offset * (period - 1)
    s = period / sigma
    weights = np.array([np.exp(-((i - m) ** 2) / (2 * s * s))
                        for i in range(period)])
    weights /= weights.sum()

    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        out[i] = np.dot(window, weights)
    return out


# ---------------------------------------------------------- momentum
def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI."""
    values = np.asarray(values, dtype=float)
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period + 1:
        return out

    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100 - (100 / (1 + rs))

    for i in range(period + 1, len(values)):
        gain = gains[i - 1]
        loss = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100 - (100 / (1 + rs))
    return out


def macd(values: np.ndarray, fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns (macd_line, signal_line, histogram).
    macd_line = EMA(fast) - EMA(slow)
    signal_line = EMA(macd_line, signal)
    histogram = macd_line - signal_line
    """
    macd_line = ema(values, fast) - ema(values, slow)
    # signal line: EMA over the macd_line, ignoring NaN warm-up
    signal_line = np.full_like(macd_line, np.nan)
    first_valid = np.argmax(~np.isnan(macd_line))
    if first_valid + signal < len(macd_line):
        clean = macd_line[first_valid:]
        sig_clean = ema(clean, signal)
        signal_line[first_valid:] = sig_clean
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def stochastic(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               k_period: int = 14, d_period: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """
    Stochastic Oscillator. Returns (%K, %D).
    Used in scalping strategy #3 (ALMA + Stochastic).
    Levels: <20 oversold, >80 overbought.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)
    k = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        window_high = high[i - k_period + 1: i + 1].max()
        window_low = low[i - k_period + 1: i + 1].min()
        rng = window_high - window_low
        if rng == 0:
            k[i] = 50.0
        else:
            k[i] = 100 * (close[i] - window_low) / rng
    d = sma(k, d_period)
    return k, d


# ---------------------------------------------------------- volatility
def true_range(high: np.ndarray, low: np.ndarray,
               close: np.ndarray) -> np.ndarray:
    """True Range — building block for ATR and Keltner Channels."""
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    return tr


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    """Average True Range (Wilder smoothing)."""
    tr = true_range(high, low, close)
    out = np.full_like(tr, np.nan)
    if len(tr) < period:
        return out
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def bollinger_bands(values: np.ndarray, period: int = 20,
                    std_mult: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (upper, middle, lower)."""
    values = np.asarray(values, dtype=float)
    middle = sma(values, period)
    out_upper = np.full_like(values, np.nan)
    out_lower = np.full_like(values, np.nan)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        sd = window.std(ddof=0)
        out_upper[i] = middle[i] + std_mult * sd
        out_lower[i] = middle[i] - std_mult * sd
    return out_upper, middle, out_lower


def keltner_channels(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                     ema_period: int = 20, atr_period: int = 10,
                     atr_mult: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Keltner Channels — volatility envelope used in scalping strategy #2.
    middle = EMA(close, 20)
    upper = middle + atr_mult * ATR
    lower = middle - atr_mult * ATR
    """
    middle = ema(close, ema_period)
    atr_vals = atr(high, low, close, atr_period)
    upper = middle + atr_mult * atr_vals
    lower = middle - atr_mult * atr_vals
    return upper, middle, lower


# ---------------------------------------------------------- price reference
def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         volume: np.ndarray) -> np.ndarray:
    """
    Volume Weighted Average Price (session-cumulative).
    Used in scalping strategy #1 (VWAP + MACD).

    Note: for true intraday VWAP you'd reset at session open.
    For continuous markets like crypto we use a rolling cumulative
    across the provided window.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    volume = np.asarray(volume, dtype=float)
    typical = (high + low + close) / 3.0
    pv = typical * volume
    cum_pv = np.cumsum(pv)
    cum_v = np.cumsum(volume)
    out = np.divide(cum_pv, cum_v,
                    out=np.full_like(cum_pv, np.nan, dtype=float),
                    where=cum_v != 0)
    return out


# ---------------------------------------------------------- aggregate helper
def compute_all(candles: list[list]) -> dict:
    """
    Convenience: takes a list of [ts, open, high, low, close, volume]
    and returns a dict of all common indicators. Strategies use what they need.
    """
    if not candles or len(candles) < 30:
        return {}
    arr = np.array(candles, dtype=float)
    high = arr[:, 2]
    low = arr[:, 3]
    close = arr[:, 4]
    vol = arr[:, 5]

    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    ema50 = ema(close, 50)
    rsi14 = rsi(close, 14)
    macd_line, macd_sig, macd_hist = macd(close)
    bb_up, bb_mid, bb_low = bollinger_bands(close)
    kc_up, kc_mid, kc_low = keltner_channels(high, low, close)
    atr14 = atr(high, low, close, 14)
    vwap_arr = vwap(high, low, close, vol)
    alma9 = alma(close, 9)
    stoch_k, stoch_d = stochastic(high, low, close)
    vol_sma = sma(vol, 20)

    return {
        "close": close, "high": high, "low": low, "volume": vol,
        "ema9": ema9, "ema21": ema21, "ema50": ema50,
        "rsi": rsi14,
        "macd": macd_line, "macd_signal": macd_sig, "macd_hist": macd_hist,
        "bb_upper": bb_up, "bb_middle": bb_mid, "bb_lower": bb_low,
        "kc_upper": kc_up, "kc_middle": kc_mid, "kc_lower": kc_low,
        "atr": atr14,
        "vwap": vwap_arr,
        "alma": alma9,
        "stoch_k": stoch_k, "stoch_d": stoch_d,
        "vol_sma": vol_sma,
    }
