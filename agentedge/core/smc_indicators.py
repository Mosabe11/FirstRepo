"""
core/smc_indicators.py — Smart Money Concepts indicators.

Calculates:
  1. Order Blocks (OB) - last institutional zones
  2. Fair Value Gaps (FVG) - imbalance zones
  3. Liquidity Levels - equal highs/lows (stop hunt zones)
  4. Point of Control (POC) - highest volume price

All functions are pure (no side effects) and tested.
Designed for hourly calculation with caching.
"""
import time
import logging
import threading
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)

# ====== Cache ======
_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600  # 1 hour


def _cache_get(key):
    with _cache_lock:
        if key in _cache:
            ts, val = _cache[key]
            if time.time() - ts < _CACHE_TTL:
                return val
            del _cache[key]
    return None


def _cache_put(key, val):
    with _cache_lock:
        _cache[key] = (time.time(), val)


# ====== Order Blocks ======

def find_order_blocks(candles, lookback=50, max_blocks=3):
    """
    Find Order Blocks: last bullish/bearish candle before a strong move.
    
    Bullish OB: last DOWN candle before a strong UP move
    Bearish OB: last UP candle before a strong DOWN move
    
    Strong move = next 3 candles move > 1.5 ATR in same direction
    
    Returns: {"bullish": [...], "bearish": [...]}
    """
    if not candles or len(candles) < lookback + 5:
        return {"bullish": [], "bearish": []}

    arr = np.array(candles[-lookback-5:], dtype=float)
    opens = arr[:, 1]
    highs = arr[:, 2]
    lows = arr[:, 3]
    closes = arr[:, 4]
    volumes = arr[:, 5]

    # Calculate ATR(14)
    tr = np.maximum.reduce([
        highs - lows,
        np.abs(highs - np.roll(closes, 1)),
        np.abs(lows - np.roll(closes, 1)),
    ])
    atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
    if atr <= 0:
        return {"bullish": [], "bearish": []}

    avg_volume = np.mean(volumes[-20:])
    bullish_obs = []
    bearish_obs = []

    # Iterate over candles, leaving room for 3-bar lookahead
    for i in range(len(arr) - 4, max(0, len(arr) - lookback - 1), -1):
        # Need 3 bars after this one
        if i + 3 >= len(arr):
            continue

        candle_range = highs[i] - lows[i]
        # Skip tiny candles
        if candle_range < atr * 0.3:
            continue

        is_bearish_candle = closes[i] < opens[i]
        is_bullish_candle = closes[i] > opens[i]

        # Strong move = price moved > 1.5 ATR in next 3 candles
        next_high = max(highs[i+1], highs[i+2], highs[i+3])
        next_low = min(lows[i+1], lows[i+2], lows[i+3])

        # Bullish OB: bearish candle followed by strong UP move
        if is_bearish_candle and (next_high - highs[i]) > atr * 1.5:
            # Volume must be at least average
            if volumes[i] >= avg_volume * 0.8:
                strength = "strong" if volumes[i] > avg_volume * 1.5 else "medium"
                # Has the OB been tested? (price returned to zone)
                tested = False
                for j in range(i + 1, len(arr)):
                    if lows[j] <= highs[i] and highs[j] >= lows[i]:
                        tested = True
                        break
                bullish_obs.append({
                    "type": "bullish",
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "mid": float((highs[i] + lows[i]) / 2),
                    "bar_index": i - (len(arr) - lookback - 5),
                    "strength": strength,
                    "tested": tested,
                    "volume_ratio": round(volumes[i] / avg_volume, 2),
                })

        # Bearish OB: bullish candle followed by strong DOWN move
        if is_bullish_candle and (lows[i] - next_low) > atr * 1.5:
            if volumes[i] >= avg_volume * 0.8:
                strength = "strong" if volumes[i] > avg_volume * 1.5 else "medium"
                tested = False
                for j in range(i + 1, len(arr)):
                    if lows[j] <= highs[i] and highs[j] >= lows[i]:
                        tested = True
                        break
                bearish_obs.append({
                    "type": "bearish",
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "mid": float((highs[i] + lows[i]) / 2),
                    "bar_index": i - (len(arr) - lookback - 5),
                    "strength": strength,
                    "tested": tested,
                    "volume_ratio": round(volumes[i] / avg_volume, 2),
                })

    # Sort by recency (newest first) and limit
    bullish_obs.sort(key=lambda x: -x["bar_index"])
    bearish_obs.sort(key=lambda x: -x["bar_index"])

    return {
        "bullish": bullish_obs[:max_blocks],
        "bearish": bearish_obs[:max_blocks],
    }


# ====== Fair Value Gaps (FVG) ======

def find_fvgs(candles, lookback=50, max_gaps=5):
    """
    Find Fair Value Gaps (3-bar imbalance pattern).
    
    Bullish FVG: high[i-1] < low[i+1] (gap up between candles i-1 and i+1)
    Bearish FVG: low[i-1] > high[i+1] (gap down)
    
    Returns: {"bullish": [...], "bearish": [...], "filled_count": N}
    """
    if not candles or len(candles) < lookback:
        return {"bullish": [], "bearish": [], "filled_count": 0}

    arr = np.array(candles[-lookback:], dtype=float)
    highs = arr[:, 2]
    lows = arr[:, 3]
    closes = arr[:, 4]

    bullish_fvgs = []
    bearish_fvgs = []
    filled_count = 0
    current_price = float(closes[-1])

    # Need at least 3 candles for FVG
    for i in range(1, len(arr) - 1):
        # Bullish FVG: gap between candle[i-1].high and candle[i+1].low
        if highs[i-1] < lows[i+1]:
            gap_top = float(lows[i+1])
            gap_bottom = float(highs[i-1])
            gap_size = gap_top - gap_bottom
            if gap_size <= 0:
                continue

            # Check if filled (price returned into the gap after creation)
            filled = False
            for j in range(i + 2, len(arr)):
                if lows[j] <= gap_top and highs[j] >= gap_bottom:
                    # If price went all the way through
                    if lows[j] <= gap_bottom:
                        filled = True
                        break
            if filled:
                filled_count += 1
                continue

            bullish_fvgs.append({
                "type": "bullish",
                "top": gap_top,
                "bottom": gap_bottom,
                "mid": (gap_top + gap_bottom) / 2,
                "size": gap_size,
                "size_pct": round(gap_size / current_price * 100, 3),
                "bar_index": i,
                "distance_pct": round((current_price - (gap_top + gap_bottom) / 2) / current_price * 100, 2),
            })

        # Bearish FVG: gap between candle[i-1].low and candle[i+1].high
        if lows[i-1] > highs[i+1]:
            gap_top = float(lows[i-1])
            gap_bottom = float(highs[i+1])
            gap_size = gap_top - gap_bottom
            if gap_size <= 0:
                continue

            filled = False
            for j in range(i + 2, len(arr)):
                if lows[j] <= gap_top and highs[j] >= gap_bottom:
                    if highs[j] >= gap_top:
                        filled = True
                        break
            if filled:
                filled_count += 1
                continue

            bearish_fvgs.append({
                "type": "bearish",
                "top": gap_top,
                "bottom": gap_bottom,
                "mid": (gap_top + gap_bottom) / 2,
                "size": gap_size,
                "size_pct": round(gap_size / current_price * 100, 3),
                "bar_index": i,
                "distance_pct": round(((gap_top + gap_bottom) / 2 - current_price) / current_price * 100, 2),
            })

    # Newest first
    bullish_fvgs.sort(key=lambda x: -x["bar_index"])
    bearish_fvgs.sort(key=lambda x: -x["bar_index"])

    return {
        "bullish": bullish_fvgs[:max_gaps],
        "bearish": bearish_fvgs[:max_gaps],
        "filled_count": filled_count,
    }


# ====== Liquidity Levels ======

def find_liquidity_levels(candles, lookback=100, tolerance_pct=0.003, min_touches=2):
    """
    Find liquidity zones: equal highs (sell-side liquidity) and equal lows (buy-side).
    These are where stop-losses cluster — magnet zones.
    
    Returns: {"buy_side": [...], "sell_side": [...]}
        buy_side = liquidity ABOVE current price (resting sell stops)
        sell_side = liquidity BELOW current price (resting buy stops)
    """
    if not candles or len(candles) < lookback:
        return {"buy_side": [], "sell_side": []}

    arr = np.array(candles[-lookback:], dtype=float)
    highs = arr[:, 2]
    lows = arr[:, 3]
    current_price = float(arr[-1, 4])

    # Find local swing points
    swing_highs = []
    swing_lows = []
    window = 3
    for i in range(window, len(arr) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            swing_highs.append((i, float(highs[i])))
        if lows[i] == min(lows[i-window:i+window+1]):
            swing_lows.append((i, float(lows[i])))

    # Cluster equal highs (within tolerance)
    def cluster(points, tol):
        if not points:
            return []
        # Sort by price
        sorted_pts = sorted(points, key=lambda x: x[1])
        groups = [[sorted_pts[0]]]
        for idx, price in sorted_pts[1:]:
            avg = sum(p for _, p in groups[-1]) / len(groups[-1])
            if abs(price - avg) / avg < tol:
                groups[-1].append((idx, price))
            else:
                groups.append([(idx, price)])
        # Keep only clusters with >= min_touches
        return [
            {
                "price": float(sum(p for _, p in g) / len(g)),
                "touches": len(g),
                "last_touch_bar": max(i for i, _ in g),
            }
            for g in groups if len(g) >= min_touches
        ]

    high_clusters = cluster(swing_highs, tolerance_pct)
    low_clusters = cluster(swing_lows, tolerance_pct)

    # Separate by position relative to current price
    buy_side = []  # ABOVE current price (sell stops resting)
    sell_side = []  # BELOW current price (buy stops resting)

    for c in high_clusters:
        if c["price"] > current_price:
            buy_side.append({
                "price": c["price"],
                "touches": c["touches"],
                "distance_pct": round((c["price"] - current_price) / current_price * 100, 2),
                "strength": "strong" if c["touches"] >= 3 else "medium",
            })

    for c in low_clusters:
        if c["price"] < current_price:
            sell_side.append({
                "price": c["price"],
                "touches": c["touches"],
                "distance_pct": round((current_price - c["price"]) / current_price * 100, 2),
                "strength": "strong" if c["touches"] >= 3 else "medium",
            })

    # Sort by closest to current price
    buy_side.sort(key=lambda x: x["distance_pct"])
    sell_side.sort(key=lambda x: x["distance_pct"])

    return {
        "buy_side": buy_side[:3],
        "sell_side": sell_side[:3],
    }


# ====== Point of Control (POC) ======

def calculate_poc(candles, lookback=50, bins=20):
    """
    Volume Profile POC: price level with highest accumulated volume.
    
    Returns: {"poc": float, "value_area_high": float, "value_area_low": float}
    """
    if not candles or len(candles) < 10:
        return None

    arr = np.array(candles[-lookback:], dtype=float)
    highs = arr[:, 2]
    lows = arr[:, 3]
    closes = arr[:, 4]
    volumes = arr[:, 5]

    price_min = float(np.min(lows))
    price_max = float(np.max(highs))
    if price_max <= price_min:
        return None

    # Divide range into bins
    bin_edges = np.linspace(price_min, price_max, bins + 1)
    bin_volumes = np.zeros(bins)

    for i in range(len(arr)):
        # Distribute candle volume across price range
        candle_low = lows[i]
        candle_high = highs[i]
        candle_vol = volumes[i]

        # Find bins this candle spans
        for b in range(bins):
            bin_low = bin_edges[b]
            bin_high = bin_edges[b + 1]
            # If candle overlaps bin
            overlap_low = max(candle_low, bin_low)
            overlap_high = min(candle_high, bin_high)
            if overlap_high > overlap_low:
                # Proportional allocation
                candle_range = candle_high - candle_low if candle_high > candle_low else 1
                bin_share = (overlap_high - overlap_low) / candle_range
                bin_volumes[b] += candle_vol * bin_share

    if bin_volumes.sum() <= 0:
        return None

    # POC = bin with highest volume
    poc_bin = int(np.argmax(bin_volumes))
    poc_price = float((bin_edges[poc_bin] + bin_edges[poc_bin + 1]) / 2)

    # Value Area = 70% of volume around POC
    total_vol = bin_volumes.sum()
    target_vol = total_vol * 0.70
    va_volumes = bin_volumes[poc_bin]
    va_low_bin = poc_bin
    va_high_bin = poc_bin

    while va_volumes < target_vol and (va_low_bin > 0 or va_high_bin < bins - 1):
        # Expand to side with more volume
        vol_below = bin_volumes[va_low_bin - 1] if va_low_bin > 0 else 0
        vol_above = bin_volumes[va_high_bin + 1] if va_high_bin < bins - 1 else 0
        if vol_above >= vol_below and va_high_bin < bins - 1:
            va_high_bin += 1
            va_volumes += vol_above
        elif va_low_bin > 0:
            va_low_bin -= 1
            va_volumes += vol_below
        else:
            break

    return {
        "poc": poc_price,
        "value_area_high": float(bin_edges[va_high_bin + 1]),
        "value_area_low": float(bin_edges[va_low_bin]),
        "current_price": float(closes[-1]),
        "price_in_value_area": bin_edges[va_low_bin] <= closes[-1] <= bin_edges[va_high_bin + 1],
    }


# ====== Unified Context ======

def get_smc_context(asset, asset_class, exchange_symbol, fetch_ohlcv_fn):
    """
    Compute all SMC indicators for an asset.
    Uses 1h timeframe for OB & POC, 15m for FVG, 4h for liquidity.
    
    Returns: {
        "order_blocks": {...},
        "fvgs": {...},
        "liquidity": {...},
        "poc": {...},
        "computed_at": timestamp,
    }
    """
    cache_key = f"smc:{asset}:{exchange_symbol}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        # Fetch data per timeframe
        candles_1h = fetch_ohlcv_fn(asset_class, exchange_symbol, "1h", 100)
        candles_15m = fetch_ohlcv_fn(asset_class, exchange_symbol, "15m", 100)
        candles_4h = fetch_ohlcv_fn(asset_class, exchange_symbol, "4h", 100)

        if not candles_1h or len(candles_1h) < 50:
            return None

        result = {
            "order_blocks": find_order_blocks(candles_1h, lookback=50),
            "fvgs": find_fvgs(candles_15m, lookback=50) if candles_15m else {"bullish": [], "bearish": [], "filled_count": 0},
            "liquidity": find_liquidity_levels(candles_4h, lookback=100) if candles_4h else {"buy_side": [], "sell_side": []},
            "poc": calculate_poc(candles_1h, lookback=50),
            "computed_at": time.time(),
        }
        _cache_put(cache_key, result)
        return result

    except Exception as e:
        logger.warning(f"SMC context failed for {asset}: {e}")
        return None


def summarize_for_ai(smc):
    """Convert SMC dict to compact text for LLM prompts."""
    if not smc:
        return "no SMC data"
    lines = []

    obs = smc.get("order_blocks", {})
    if obs.get("bullish"):
        ob = obs["bullish"][0]
        lines.append(f"Bullish OB: {ob['low']:.2f}-{ob['high']:.2f} ({ob['strength']}, tested={ob['tested']})")
    if obs.get("bearish"):
        ob = obs["bearish"][0]
        lines.append(f"Bearish OB: {ob['low']:.2f}-{ob['high']:.2f} ({ob['strength']}, tested={ob['tested']})")

    fvgs = smc.get("fvgs", {})
    if fvgs.get("bullish"):
        f = fvgs["bullish"][0]
        lines.append(f"Bullish FVG: {f['bottom']:.2f}-{f['top']:.2f} (unfilled, {f['distance_pct']}% away)")
    if fvgs.get("bearish"):
        f = fvgs["bearish"][0]
        lines.append(f"Bearish FVG: {f['bottom']:.2f}-{f['top']:.2f} (unfilled, {f['distance_pct']}% away)")

    liq = smc.get("liquidity", {})
    if liq.get("buy_side"):
        l = liq["buy_side"][0]
        lines.append(f"Buy-side liquidity above: {l['price']:.2f} ({l['touches']} touches, {l['distance_pct']}% up)")
    if liq.get("sell_side"):
        l = liq["sell_side"][0]
        lines.append(f"Sell-side liquidity below: {l['price']:.2f} ({l['touches']} touches, {l['distance_pct']}% down)")

    poc = smc.get("poc")
    if poc:
        lines.append(f"POC: {poc['poc']:.2f} (VA: {poc['value_area_low']:.2f}-{poc['value_area_high']:.2f})")

    return "; ".join(lines) if lines else "no significant levels"
