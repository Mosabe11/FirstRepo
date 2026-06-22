"""
news/sentiment.py — Fetch recent news + AI sentiment analysis.

Uses CryptoPanic (free tier) for crypto and DeepSeek for sentiment scoring.
For Forex/Metals it uses general financial news search.

Cached for 10 minutes per asset to limit API calls.
"""
import time
import json
import logging
import threading
import requests
from urllib.parse import quote

from config import settings
from core.notify import tg_send

logger = logging.getLogger(__name__)

_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 600  # 10 minutes


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


def _fetch_crypto_news(symbol):
    """Free CryptoPanic API — public posts endpoint, no key needed."""
    try:
        r = requests.get(
            "https://cryptopanic.com/api/free/v1/posts/",
            params={"currencies": symbol, "public": "true", "kind": "news"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json().get("results", [])
        return [{"title": p.get("title", ""), "url": p.get("url", "")} for p in data[:5]]
    except Exception as e:
        logger.warning(f"CryptoPanic fetch failed {symbol}: {e}")
        return []


def _analyze_sentiment(asset, headlines):
    """Ask DeepSeek to score sentiment from headlines."""
    if not settings.DEEPSEEK_KEY or not headlines:
        return None
    titles = "\n".join(f"- {h['title']}" for h in headlines)
    try:
        r = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content":
                     "You are a financial news sentiment analyst. "
                     "Score sentiment from -100 (extremely bearish) to +100 (extremely bullish). "
                     "Respond ONLY in JSON: "
                     '{"score":-100..100,"label":"BULLISH|BEARISH|NEUTRAL","summary":"short"}'},
                    {"role": "user", "content":
                     f"Asset: {asset}\nRecent headlines:\n{titles}\n\nScore the sentiment."},
                ],
                "temperature": 0.2,
                "max_tokens": 200,
            },
            timeout=12,
        )
        if r.status_code != 200:
            return None
        content = r.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()
        return json.loads(content)
    except Exception as e:
        logger.warning(f"Sentiment analysis failed: {e}")
        return None


def get_sentiment(asset, asset_class):
    """
    Get news + sentiment for an asset.
    Returns dict {score, label, summary, headlines} or None on failure.
    For non-crypto assets (forex/metals) returns None — no news source yet.
    """
    if asset_class not in ("crypto", "binance"):
        return None  # forex/metals not supported yet

    cached = _cache_get(asset)
    if cached is not None:
        return cached

    headlines = _fetch_crypto_news(asset)
    if not headlines:
        return None

    sentiment = _analyze_sentiment(asset, headlines)
    if not sentiment:
        return None

    result = {
        "score": float(sentiment.get("score", 0)),
        "label": str(sentiment.get("label", "NEUTRAL")).upper(),
        "summary": str(sentiment.get("summary", ""))[:200],
        "headlines": [h["title"] for h in headlines[:3]],
    }
    _cache_put(asset, result)
    return result


def is_blocking_news(asset, asset_class, direction):
    """
    Returns (should_block, reason) — True means refuse the trade due to news.
    Logic: don't go LONG when very bearish, don't go SHORT when very bullish.
    """
    sentiment = get_sentiment(asset, asset_class)
    if not sentiment:
        return False, "no news data"

    score = sentiment["score"]
    if direction == "LONG" and score < -50:
        tg_send(
            f"📰 *News Block* `{asset}` LONG rejected\n"
            f"Sentiment: {score:+.0f} ({sentiment['label']})\n"
            f"_{sentiment['summary']}_",
            category="ai",
        )
        return True, f"news strongly bearish ({score:+.0f})"

    if direction == "SHORT" and score > 50:
        tg_send(
            f"📰 *News Block* `{asset}` SHORT rejected\n"
            f"Sentiment: {score:+.0f} ({sentiment['label']})\n"
            f"_{sentiment['summary']}_",
            category="ai",
        )
        return True, f"news strongly bullish ({score:+.0f})"

    return False, "news aligned"
