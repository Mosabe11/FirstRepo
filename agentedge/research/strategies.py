"""
research/strategies.py — candidate signal generators for backtesting.

Each generator is a function: signal(arr) -> dict | None, where `arr` is a
numpy array of candles [ts, o, h, l, c, v] containing ONLY bars up to and
including the current closed bar (the engine never passes future bars, so
look-ahead is structurally impossible). Returns:

    {"direction": "LONG"|"SHORT", "atr": float, "reason": str}

The engine sets the ATR-based stop and the R-multiple target uniformly, so a
strategy only has to decide direction + hand back the ATR it measured.
"""
from __future__ import annotations
import numpy as np


# ----------------------------- indicators (numpy) -----------------------------
def _ema(v: np.ndarray, p: int) -> np.ndarray:
    out = np.full(len(v), np.nan)
    if len(v) < p:
        return out
    a = 2.0 / (p + 1.0)
    out[p - 1] = v[:p].mean()
    for i in range(p, len(v)):
        out[i] = a * v[i] + (1 - a) * out[i - 1]
    return out


def _atr(h, l, c, p=14) -> np.ndarray:
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(len(tr), np.nan)
    if len(tr) < p:
        return out
    out[p - 1] = tr[:p].mean()
    for i in range(p, len(tr)):
        out[i] = (out[i - 1] * (p - 1) + tr[i]) / p
    return out


def _adx(h, l, c, p=14) -> np.ndarray:
    n = len(c)
    out = np.full(n, np.nan)
    if n < 2 * p:
        return out
    up = h[1:] - h[:-1]
    dn = l[:-1] - l[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = c[:-1]
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - pc), np.abs(l[1:] - pc)])

    def wilder(x):
        s = np.full(len(x), np.nan)
        s[p - 1] = x[:p].sum()
        for i in range(p, len(x)):
            s[i] = s[i - 1] - s[i - 1] / p + x[i]
        return s

    atr_ = wilder(tr)
    pdi = 100 * wilder(plus_dm) / atr_
    mdi = 100 * wilder(minus_dm) / atr_
    dx = 100 * np.abs(pdi - mdi) / np.where((pdi + mdi) == 0, np.nan, pdi + mdi)
    adx = np.full(len(dx), np.nan)
    first = p - 1 + p
    if first < len(dx):
        adx[first] = np.nanmean(dx[p - 1:first + 1])
        for i in range(first + 1, len(dx)):
            adx[i] = (adx[i - 1] * (p - 1) + dx[i]) / p
    # shift back to align with original index (dx is length n-1)
    out[1:] = adx
    return out


def _macd(c, fast=12, slow=26, sig=9):
    ml = _ema(c, fast) - _ema(c, slow)
    sl = np.full(len(ml), np.nan)
    fv = int(np.argmax(~np.isnan(ml)))
    if fv + sig < len(ml):
        sl[fv:] = _ema(ml[fv:], sig)
    return ml, sl, ml - sl


def _vwap(h, l, c, v):
    typ = (h + l + c) / 3.0
    cv = np.cumsum(v)
    return np.divide(np.cumsum(typ * v), cv, out=np.full(len(c), np.nan), where=cv != 0)


# ----------------------------- baseline: vwap_macd ----------------------------
def vwap_macd_signal(arr: np.ndarray) -> dict | None:
    """Faithful re-implementation of strategies/vwap_macd.py (the live baseline)."""
    if len(arr) < 60:
        return None
    h, l, c, v = arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5]
    vw = _vwap(h, l, c, v)
    ml, sl, hist = _macd(c)
    a = _atr(h, l, c, 14)
    if not np.all(np.isfinite([vw[-1], ml[-1], sl[-1], ml[-2], sl[-2], hist[-1], a[-1]])):
        return None
    bull = (ml[-1] > sl[-1]) and (ml[-2] <= sl[-2]) and hist[-1] > 0 and c[-1] > vw[-1]
    bear = (ml[-1] < sl[-1]) and (ml[-2] >= sl[-2]) and hist[-1] < 0 and c[-1] < vw[-1]
    if bull:
        return {"direction": "LONG", "atr": float(a[-1]), "reason": "vwap+macd bull"}
    if bear:
        return {"direction": "SHORT", "atr": float(a[-1]), "reason": "vwap+macd bear"}
    return None


# ----------------------- new: regime-filtered trend ---------------------------
def make_regime_trend(adx_min: float = 22.0, donchian: int = 20,
                      ema_trend: int = 50, ema_fast: int = 21):
    """Donchian breakout, taken ONLY in a confirmed trend regime:
    price on the trend side of EMA50, fast EMA stacked with slow EMA, and
    ADX above `adx_min` (a real trend, not chop). Stands aside otherwise —
    which is the whole point: a momentum signal bleeds in sideways markets."""
    need = max(donchian, ema_trend, 30) + 30

    def sig(arr: np.ndarray) -> dict | None:
        if len(arr) < need:
            return None
        h, l, c = arr[:, 2], arr[:, 3], arr[:, 4]
        ef, es = _ema(c, ema_fast), _ema(c, ema_trend)
        adx, a = _adx(h, l, c, 14), _atr(h, l, c, 14)
        if not np.all(np.isfinite([ef[-1], es[-1], adx[-1], a[-1]])):
            return None
        # prior-window Donchian (exclude current bar -> no self-reference)
        dh = float(np.max(h[-donchian - 1:-1]))
        dl = float(np.min(l[-donchian - 1:-1]))
        trending = adx[-1] >= adx_min
        if trending and c[-1] > es[-1] and ef[-1] > es[-1] and c[-1] >= dh:
            return {"direction": "LONG", "atr": float(a[-1]),
                    "reason": f"trend-up breakout adx={adx[-1]:.0f}"}
        if trending and c[-1] < es[-1] and ef[-1] < es[-1] and c[-1] <= dl:
            return {"direction": "SHORT", "atr": float(a[-1]),
                    "reason": f"trend-dn breakout adx={adx[-1]:.0f}"}
        return None

    sig.__name__ = f"regime_trend(adx>={adx_min:g},don={donchian})"
    return sig
