"""
core/levels.py — Support/Resistance level detection.

Algorithm:
  1. Find swing highs/lows on 1h timeframe (last 200 bars)
  2. Cluster nearby levels (within 0.5% of each other)
  3. Score by: touch count, recency, volume at level
  4. Return top 3 support + top 3 resistance
"""
import time
import logging
import threading
import numpy as np

logger = logging.getLogger(__name__)

_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600  # ساعة


def _find_swing_points(highs, lows, window=5):
    """يلاقي swing highs/lows باستخدام pivot windows."""
    swing_highs = []
    swing_lows = []
    for i in range(window, len(highs) - window):
        # Swing high: أعلى من اللي قبله وبعده بـ window
        if highs[i] == max(highs[i-window:i+window+1]):
            swing_highs.append((i, float(highs[i])))
        if lows[i] == min(lows[i-window:i+window+1]):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def _cluster_levels(levels, tolerance_pct=0.005):
    """يجمع levels قريبة من بعض."""
    if not levels:
        return []
    sorted_levels = sorted(levels, key=lambda x: x[1])
    clusters = [[sorted_levels[0]]]

    for idx, price in sorted_levels[1:]:
        last_cluster_avg = sum(p for _, p in clusters[-1]) / len(clusters[-1])
        if abs(price - last_cluster_avg) / last_cluster_avg < tolerance_pct:
            clusters[-1].append((idx, price))
        else:
            clusters.append([(idx, price)])

    # Return (avg_price, touch_count, last_touch_idx)
    return [
        (
            sum(p for _, p in c) / len(c),  # avg price
            len(c),                          # touch count
            max(i for i, _ in c),            # last touch idx
        )
        for c in clusters
    ]


def compute_levels(asset_class, exchange_symbol, fetch_ohlcv_fn, current_price=None):
    """
    Returns dict:
      {
        "support": [(price, score, distance_pct), ...],
        "resistance": [(price, score, distance_pct), ...],
        "current_price": float
      }
    """
    cache_key = f"levels:{asset_class}:{exchange_symbol}"
    with _cache_lock:
        if cache_key in _cache:
            ts, val = _cache[cache_key]
            if time.time() - ts < _CACHE_TTL:
                return val

    candles = fetch_ohlcv_fn(asset_class, exchange_symbol, "1h", 200)
    if not candles or len(candles) < 50:
        return None

    arr = np.array(candles, dtype=float)
    highs = arr[:, 2]
    lows = arr[:, 3]
    closes = arr[:, 4]
    cp = current_price or float(closes[-1])

    # 1. Find swings
    swing_highs, swing_lows = _find_swing_points(highs, lows, window=5)

    # 2. Cluster
    res_clusters = _cluster_levels(swing_highs, tolerance_pct=0.008)
    sup_clusters = _cluster_levels(swing_lows, tolerance_pct=0.008)

    # 3. Score: touches × recency_weight
    total_bars = len(highs)
    def score(cluster):
        price, touches, last_idx = cluster
        recency = (last_idx / total_bars)  # 0..1 (newer = higher)
        return touches * 10 + recency * 30

    res_scored = [
        (p, score(c), (p - cp) / cp * 100)
        for c in res_clusters
        for p in [c[0]]
        if p > cp  # فقط فوق السعر الحالي
    ]
    sup_scored = [
        (p, score(c), (cp - p) / cp * 100)
        for c in sup_clusters
        for p in [c[0]]
        if p < cp  # فقط تحت السعر الحالي
    ]

    # 4. Top 3 من كل واحد
    res_scored.sort(key=lambda x: x[2])  # الأقرب فوق
    sup_scored.sort(key=lambda x: -x[2])  # الأقرب تحت (سالب أكبر = أبعد)
    sup_scored.sort(key=lambda x: x[2])  # الأقرب تحت

    result = {
        "support": sup_scored[:3],
        "resistance": res_scored[:3],
        "current_price": cp,
    }
    with _cache_lock:
        _cache[cache_key] = (time.time(), result)
    return result


def detect_breakout(asset_class, exchange_symbol, fetch_ohlcv_fn,
                    current_price, prev_price):
    """
    يكتشف لو السعر اخترق support/resistance.
    Returns: (event_type, level_price) أو (None, None)
      event_type: "RESISTANCE_BREAK" | "SUPPORT_BREAK" | "RESISTANCE_REJECT" | "SUPPORT_BOUNCE"
    """
    levels = compute_levels(asset_class, exchange_symbol, fetch_ohlcv_fn, current_price)
    if not levels:
        return None, None

    # تحقق resistance breakouts
    for res_price, score, _ in levels["resistance"]:
        if prev_price < res_price and current_price >= res_price:
            return "RESISTANCE_BREAK", res_price
        # Rejection: قرب من resistance لكن ارتد
        if abs(current_price - res_price) / res_price < 0.003 and current_price < prev_price:
            return "RESISTANCE_REJECT", res_price

    # تحقق support breaks
    for sup_price, score, _ in levels["support"]:
        if prev_price > sup_price and current_price <= sup_price:
            return "SUPPORT_BREAK", sup_price
        # Bounce: قرب من support وارتد
        if abs(current_price - sup_price) / sup_price < 0.003 and current_price > prev_price:
            return "SUPPORT_BOUNCE", sup_price

    return None, None
