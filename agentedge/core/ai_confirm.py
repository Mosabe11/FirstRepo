"""
core/ai_confirm.py
------------------
DeepSeek-based BUY/SELL/HOLD confirmation for proposed trades.

- 5-minute cache on (asset, direction, rounded-price) to avoid spam
- Returns (decision, confidence) or (None, 0) on failure
- Strategies/bots are expected to fall back to pure technical edge
  if the AI is unavailable
"""

from __future__ import annotations
import time
import json
import logging
import threading
import requests

from config import settings

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, tuple[str, float]]] = {}
_cache_lock = threading.Lock()

_API_URL = "https://api.deepseek.com/v1/chat/completions"


def _cache_get(key: str):
    with _cache_lock:
        if key in _cache:
            ts, val = _cache[key]
            if time.time() - ts < settings.AI_CACHE_TTL_SECONDS:
                return val
            del _cache[key]
    return None


def _cache_put(key: str, val: tuple[str, float]):
    with _cache_lock:
        _cache[key] = (time.time(), val)


def confirm_trade(asset: str, direction: str, price: float,
                  indicators: dict) -> tuple[str | None, float]:
    """
    Ask DeepSeek to confirm a proposed LONG/SHORT.
    Returns (decision, confidence_0_100).
      decision ∈ {"BUY", "SELL", "HOLD", None}
      None means API failed → caller should fall back to technical edge.
    """
    if not settings.DEEPSEEK_KEY:
        return None, 0.0

    # cache key: bucketed price to ~1% to allow some reuse
    px_bucket = round(price * 100) / 100 if price < 100 else round(price)
    key = f"{asset}:{direction}:{px_bucket}"
    cached = _cache_get(key)
    if cached:
        return cached

    prompt = _build_prompt(asset, direction, price, indicators)
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
                     "You are a disciplined crypto trading risk reviewer. "
                     "Respond ONLY with valid JSON: "
                     '{"decision":"BUY|SELL|HOLD","confidence":0-100,"reason":"..."}'},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 200,
            },
            timeout=12,
        )
        if r.status_code != 200:
            logger.warning(f"DeepSeek HTTP {r.status_code}: {r.text[:200]}")
            return None, 0.0
        content = r.json()["choices"][0]["message"]["content"].strip()
        # tolerate code-fenced JSON
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)
        decision = str(data.get("decision", "HOLD")).upper()
        confidence = float(data.get("confidence", 0))
        if decision not in ("BUY", "SELL", "HOLD"):
            decision = "HOLD"
        result = (decision, confidence)
        _cache_put(key, result)
        return result
    except Exception as e:
        logger.warning(f"DeepSeek call failed: {e}")
        return None, 0.0


def _build_prompt(asset: str, direction: str, price: float,
                  indicators: dict) -> str:
    keep = ["rsi", "macd_hist", "ema9", "ema21", "ema50",
            "bb_upper", "bb_lower", "vwap", "atr"]
    snap = {}
    for k in keep:
        v = indicators.get(k)
        if v is None:
            continue
        try:
            # take last value if array-like
            v_last = v[-1] if hasattr(v, "__len__") else v
            snap[k] = round(float(v_last), 6)
        except Exception:
            pass

    return (
        f"Asset: {asset}\n"
        f"Proposed direction: {direction}\n"
        f"Current price: {price}\n"
        f"Latest indicators: {json.dumps(snap)}\n\n"
        f"Should we execute this trade now, or wait? "
        f"Respond with JSON only."
    )
