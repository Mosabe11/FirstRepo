"""
council/market_analyst.py — AI-driven Multi-Timeframe Market Analyst.

بدل من EMA crossover ثابت، AI يحلل:
  - 5m, 15m, 1h, 4h candles
  - Trend strength
  - Support/Resistance proximity
  - Volume profile
  - Market structure (HH/HL أو LH/LL)

ويعطي:
  - bias: LONG / SHORT / NEUTRAL
  - strength: 0-100
  - reasoning: لماذا
"""
import json
import time
import logging
import threading
import requests
import numpy as np

from config import settings

logger = logging.getLogger(__name__)
_API_URL = "https://api.deepseek.com/v1/chat/completions"

_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 دقائق


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


def _summarize_candles(candles, n_last=10):
    """يحول candles لنص مختصر يفهمه الـ AI."""
    if not candles or len(candles) < n_last:
        return "insufficient data"
    arr = np.array(candles[-n_last:], dtype=float)
    closes = arr[:, 4]
    highs = arr[:, 2]
    lows = arr[:, 3]
    vols = arr[:, 5]

    first, last = closes[0], closes[-1]
    change_pct = (last - first) / first * 100 if first else 0
    high_val = float(highs.max())
    low_val = float(lows.min())
    vol_avg = float(vols.mean())
    vol_recent = float(vols[-3:].mean())
    vol_ratio = vol_recent / vol_avg if vol_avg else 1

    # Higher highs / Lower lows pattern
    pattern = ""
    if closes[-1] > closes[-3] > closes[-5]:
        pattern = "uptrend (HH)"
    elif closes[-1] < closes[-3] < closes[-5]:
        pattern = "downtrend (LL)"
    else:
        pattern = "ranging"

    return (
        f"Last {n_last} bars: open={first:.4f}, close={last:.4f} ({change_pct:+.2f}%), "
        f"high={high_val:.4f}, low={low_val:.4f}, "
        f"vol_ratio={vol_ratio:.2f}x avg, pattern={pattern}"
    )


def analyze_market(asset, asset_class, fetch_ohlcv_fn):
    """
    AI-driven multi-timeframe analysis.
    Returns: (bias, strength, reasoning) أو (None, 0, "")
    """
    if not settings.DEEPSEEK_KEY:
        return None, 0, "no AI key"

    cache_key = f"market_analyst:{asset}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    # نجمع بيانات من 4 timeframes
    tf_data = {}
    for tf in ["5m", "15m", "1h", "4h"]:
        try:
            limit = 30 if tf in ("5m", "15m") else 50
            from config.watchlist import to_dict
            cfg = to_dict().get(asset)
            if not cfg:
                continue
            candles = fetch_ohlcv_fn(asset_class, cfg.exchange_symbol, tf, limit)
            if candles and len(candles) >= 10:
                tf_data[tf] = _summarize_candles(candles, n_last=10)
        except Exception as e:
            logger.warning(f"MarketAnalyst fetch {tf}: {e}")

    if not tf_data:
        return None, 0, "no data"

    # Build prompt
    tf_summary = "\n".join(f"  {tf}: {desc}" for tf, desc in tf_data.items())

    try:
        r = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content":
                        "You are a professional crypto/forex market analyst. "
                        "Analyze multi-timeframe price action and determine the "
                        "directional bias. Be decisive — NEUTRAL is acceptable only "
                        "when timeframes truly conflict. "
                        "Weight 1h and 4h higher than 5m and 15m (they show the real trend). "
                        "Respond ONLY in JSON: "
                        '{"bias":"LONG|SHORT|NEUTRAL","strength":0-100,"reasoning":"short explanation"}'
                    },
                    {"role": "user", "content":
                        f"Asset: {asset}\n\nMulti-timeframe data:\n{tf_summary}\n\n"
                        f"What is the dominant directional bias right now?"
                    },
                ],
                "temperature": 0.2,
                "max_tokens": 250,
            },
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning(f"MarketAnalyst HTTP {r.status_code}")
            return None, 0, "API error"

        content = r.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            import re
            bias = re.search(r'"bias"\s*:\s*"(\w+)"', content)
            strength = re.search(r'"strength"\s*:\s*(\d+)', content)
            if not bias:
                return None, 0, "parse failed"
            data = {
                "bias": bias.group(1).upper(),
                "strength": int(strength.group(1)) if strength else 50,
                "reasoning": "",
            }

        bias = str(data.get("bias", "NEUTRAL")).upper()
        strength = float(data.get("strength", 0))
        reasoning = str(data.get("reasoning", ""))[:200]

        if bias not in ("LONG", "SHORT", "NEUTRAL"):
            bias = "NEUTRAL"

        result = (bias, strength, reasoning)
        _cache_put(cache_key, result)
        return result

    except Exception as e:
        logger.warning(f"MarketAnalyst error: {e}")
        return None, 0, str(e)


def check_alignment(asset, asset_class, direction, fetch_ohlcv_fn):
    """
    تحقق إذا الـ direction المقترح يتفق مع تحليل السوق.
    Returns: (allowed, reason)
    """
    bias, strength, reasoning = analyze_market(asset, asset_class, fetch_ohlcv_fn)

    if bias is None:
        return True, "no analysis"  # نسمح في حالة فشل

    if bias == "NEUTRAL":
        # في حالة NEUTRAL، نسمح إذا الـ strength منخفض (مش متأكد)
        if strength >= 70:
            return False, f"NEUTRAL strong (str {strength:.0f}): {reasoning[:80]}"
        return True, f"NEUTRAL weak, allowed"

    if bias != direction:
        return False, f"AI bias {bias} (str {strength:.0f}): {reasoning[:80]}"

    return True, f"AI confirms {bias} (str {strength:.0f})"
