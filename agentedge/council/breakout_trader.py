"""
council/breakout_trader.py — AI agent that decides whether to trade a breakout.

Triggered when monitor.py detects a breakout on S/R level.
Agent analyzes:
  - Type of event (BREAK vs REJECT/BOUNCE)
  - Recent price action context
  - Volume confirmation
  - Multi-TF alignment

Returns: (should_trade, direction, confidence, reasoning, sl, tp)
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

_rate_lock = threading.Lock()
_call_count = 0
_call_reset = 0
_MAX_CALLS_PER_HOUR = 15


def _check_rate_limit():
    global _call_count, _call_reset
    with _rate_lock:
        now = time.time()
        if now - _call_reset > 3600:
            _call_count = 0
            _call_reset = now
        if _call_count >= _MAX_CALLS_PER_HOUR:
            return False
        _call_count += 1
        return True


def _build_context(asset, event, level, current_price, candles_5m, candles_1h):
    """يبني نص مختصر يصف الـ situation للـ AI."""
    arr5 = np.array(candles_5m[-20:], dtype=float) if candles_5m else None
    arr1h = np.array(candles_1h[-20:], dtype=float) if candles_1h else None

    context = {
        "event": event,
        "level": level,
        "current_price": current_price,
        "distance_pct": (current_price - level) / level * 100,
    }

    if arr5 is not None:
        closes5 = arr5[:, 4]
        vols5 = arr5[:, 5]
        context["5m"] = {
            "last_5_change_pct": (closes5[-1] - closes5[-5]) / closes5[-5] * 100,
            "vol_recent_vs_avg": float(vols5[-3:].mean() / vols5[:-3].mean()) if vols5[:-3].mean() else 1,
            "last_3_pattern": "up" if closes5[-1] > closes5[-2] > closes5[-3] else
                              "down" if closes5[-1] < closes5[-2] < closes5[-3] else "mixed",
        }

    if arr1h is not None:
        closes1h = arr1h[:, 4]
        context["1h"] = {
            "last_5_change_pct": (closes1h[-1] - closes1h[-5]) / closes1h[-5] * 100,
            "trend": "up" if closes1h[-1] > closes1h[-10] else "down",
        }

    return context


def decide_breakout(asset, asset_class, event, level, current_price, fetch_ohlcv_fn):
    """
    AI يقرر هل نتداول على هاد الـ breakout.

    Returns: dict with:
      - should_trade: bool
      - direction: "LONG" | "SHORT"
      - confidence: 0-100
      - reasoning: str
      - suggested_sl: float
      - suggested_tp: float
    """
    if not settings.DEEPSEEK_KEY:
        return {"should_trade": False, "reasoning": "no AI key"}

    if not _check_rate_limit():
        logger.debug("Breakout Trader rate limit hit")
        return {"should_trade": False, "reasoning": "rate limit"}

    try:
        from config.watchlist import to_dict
        cfg = to_dict().get(asset)
        if not cfg:
            return {"should_trade": False, "reasoning": "asset not found"}

        candles_5m = fetch_ohlcv_fn(asset_class, cfg.exchange_symbol, "5m", 50)
        candles_1h = fetch_ohlcv_fn(asset_class, cfg.exchange_symbol, "1h", 50)
        ctx = _build_context(asset, event, level, current_price, candles_5m, candles_1h)
    except Exception as e:
        return {"should_trade": False, "reasoning": f"context build failed: {e}"}

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
                        "You are a professional breakout trader. A price has just interacted with a key "
                        "support/resistance level. Decide whether this is a TRADEABLE setup. "
                        "Be selective — false breakouts are common. Look for: "
                        "1) Volume confirmation on the move "
                        "2) Multi-timeframe alignment "
                        "3) Clean impulsive move (not choppy) "
                        "Event types: "
                        "  - RESISTANCE_BREAK: price crossed above resistance (potential LONG) "
                        "  - SUPPORT_BREAK: price crossed below support (potential SHORT) "
                        "  - RESISTANCE_REJECT: price rejected at resistance (potential SHORT) "
                        "  - SUPPORT_BOUNCE: price bounced off support (potential LONG) "
                        "Respond ONLY in JSON: "
                        '{"should_trade":true|false,"direction":"LONG|SHORT",'
                        '"confidence":0-100,"reasoning":"short",'
                        '"sl_pct":0.005-0.02,"tp_pct":0.01-0.05}'
                    },
                    {"role": "user", "content": json.dumps(ctx)},
                ],
                "temperature": 0.2,
                "max_tokens": 300,
            },
            timeout=15,
        )
        if r.status_code != 200:
            return {"should_trade": False, "reasoning": f"API HTTP {r.status_code}"}

        content = r.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            import re
            should = re.search(r'"should_trade"\s*:\s*(true|false)', content)
            direction = re.search(r'"direction"\s*:\s*"(\w+)"', content)
            conf = re.search(r'"confidence"\s*:\s*(\d+)', content)
            data = {
                "should_trade": should and should.group(1) == "true",
                "direction": direction.group(1).upper() if direction else "LONG",
                "confidence": int(conf.group(1)) if conf else 0,
                "reasoning": "parse fallback",
                "sl_pct": 0.01,
                "tp_pct": 0.025,
            }

        # Sanity defaults
        data.setdefault("sl_pct", 0.01)
        data.setdefault("tp_pct", 0.025)
        data["sl_pct"] = max(0.003, min(0.03, float(data["sl_pct"])))
        data["tp_pct"] = max(0.008, min(0.06, float(data["tp_pct"])))

        # Compute absolute SL/TP from current price
        direction = str(data.get("direction", "LONG")).upper()
        if direction == "LONG":
            data["suggested_sl"] = current_price * (1 - data["sl_pct"])
            data["suggested_tp"] = current_price * (1 + data["tp_pct"])
        else:
            data["suggested_sl"] = current_price * (1 + data["sl_pct"])
            data["suggested_tp"] = current_price * (1 - data["tp_pct"])

        return data

    except Exception as e:
        logger.warning(f"BreakoutTrader error: {e}")
        return {"should_trade": False, "reasoning": str(e)}
