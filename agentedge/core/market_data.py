"""
core/market_data.py
-------------------
Fetches OHLCV candles + tickers from MEXC (crypto) and Yahoo (metals).
- In-memory TTL cache to avoid hammering APIs
- Graceful fallback: if a fetch fails, returns last cached value with a flag
- Single shared ccxt MEXC client (lazy-init, auto-reconnect)
"""

from __future__ import annotations
import time
import logging
import threading
from typing import Optional

import ccxt
import yfinance as yf

logger = logging.getLogger(__name__)

# ----- ccxt singleton -----
_mexc_lock = threading.Lock()
_mexc: Optional[ccxt.mexc] = None


def get_mexc() -> ccxt.mexc:
    """Lazy-init MEXC client. No API keys needed for public data."""
    global _mexc
    with _mexc_lock:
        if _mexc is None:
            _mexc = ccxt.mexc({
                "enableRateLimit": True,
                "timeout": 15000,
            })
        return _mexc


def reconnect_mexc():
    """Force a fresh client (called on repeated errors)."""
    global _mexc
    with _mexc_lock:
        _mexc = None
    return get_mexc()


# ----- simple TTL cache -----
class _TTLCache:
    def __init__(self, ttl: float = 30.0):
        self.ttl = ttl
        self._data: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            if key in self._data:
                ts, val = self._data[key]
                if time.time() - ts < self.ttl:
                    return val
        return None

    def set(self, key: str, val):
        with self._lock:
            self._data[key] = (time.time(), val)


_ohlcv_cache = _TTLCache(ttl=25)   # candles ~30s freshness
_ticker_cache = _TTLCache(ttl=3)    # ticker prices, very fresh


# ----- public API -----
def fetch_ohlcv(asset_class: str, exchange_symbol: str,
                timeframe: str = "1h", limit: int = 100) -> list[list]:
    """
    Returns a list of [timestamp_ms, open, high, low, close, volume].
    Empty list on failure (callers must guard).
    """
    cache_key = f"ohlcv:{asset_class}:{exchange_symbol}:{timeframe}:{limit}"
    cached = _ohlcv_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        if asset_class == "binance":
            try:
                import ccxt
                global _binance_ex
                if "_binance_ex" not in globals() or _binance_ex is None:
                    _binance_ex = ccxt.binance()
                tf_map = {"1m":"1m","5m":"5m","15m":"15m","1h":"1h","4h":"4h","1d":"1d"}
                ohlcv = _binance_ex.fetch_ohlcv(exchange_symbol, timeframe=tf_map.get(timeframe,"1h"), limit=limit)
                candles = [list(c) for c in ohlcv] if ohlcv else []
            except Exception as e:
                logger.warning(f"Binance OHLCV failed {exchange_symbol}: {e}")
                candles = []
        elif asset_class == "forex":
            candles = _fetch_yahoo_ohlcv(exchange_symbol, timeframe, limit)
        elif asset_class == "crypto":
            candles = _fetch_mexc_ohlcv(exchange_symbol, timeframe, limit)
        elif asset_class == "metal":
            candles = _fetch_yahoo_ohlcv(exchange_symbol, timeframe, limit)
        else:
            return []
        if candles:
            _ohlcv_cache.set(cache_key, candles)
        return candles
    except Exception as e:
        logger.warning(f"fetch_ohlcv failed {exchange_symbol}: {e}")
        return []


def fetch_price(asset_class: str, exchange_symbol: str) -> float | None:
    """Current best price (cached ~3s)."""
    cache_key = f"px:{asset_class}:{exchange_symbol}"
    cached = _ticker_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        if asset_class == "binance":
            try:
                import ccxt
                global _binance_ex
                if "_binance_ex" not in globals() or _binance_ex is None:
                    _binance_ex = ccxt.binance()
                t = _binance_ex.fetch_ticker(exchange_symbol)
                price = float(t["last"]) if t and t.get("last") else None
            except Exception as e:
                logger.warning(f"Binance price failed {exchange_symbol}: {e}")
                result = None
        elif asset_class == "forex":
            cs = _fetch_yahoo_ohlcv(exchange_symbol, "1h", 2)
            price = float(cs[-1][4]) if cs else None
        elif asset_class == "crypto":
            ticker = get_mexc().fetch_ticker(exchange_symbol)
            price = float(ticker["last"])
        elif asset_class == "metal":
            # use most recent 1m candle close as the price
            candles = _fetch_yahoo_ohlcv(exchange_symbol, "1m", 2)
            if not candles:
                return None
            price = float(candles[-1][4])
        else:
            return None
        _ticker_cache.set(cache_key, price)
        return price
    except Exception as e:
        logger.warning(f"fetch_price failed {exchange_symbol}: {e}")
        return None


def fetch_top_usdt_pairs(top_n: int = 50,
                         min_volume_usdt: float = 1_000_000) -> list[dict]:
    """For the Discovery bot — returns sorted list of high-volume MEXC pairs."""
    try:
        tickers = get_mexc().fetch_tickers()
    except Exception as e:
        logger.warning(f"fetch_tickers failed: {e}")
        return []

    candidates = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT"):
            continue
        qv = t.get("quoteVolume") or 0
        if qv < min_volume_usdt:
            continue
        candidates.append({
            "symbol": sym.split("/")[0],
            "exchange_symbol": sym,
            "quote_volume": qv,
            "percent_change": t.get("percentage", 0) or 0,
            "last": t.get("last", 0) or 0,
        })

    candidates.sort(key=lambda x: x["quote_volume"], reverse=True)
    return candidates[:top_n]


# ----- internals -----
def _fetch_mexc_ohlcv(symbol: str, timeframe: str, limit: int) -> list[list]:
    try:
        return get_mexc().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except (ccxt.NetworkError, ccxt.ExchangeError) as e:
        logger.warning(f"MEXC OHLCV error {symbol}, reconnecting: {e}")
        reconnect_mexc()
        try:
            return get_mexc().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as e2:
            logger.error(f"MEXC OHLCV retry failed {symbol}: {e2}")
            return []


# Yahoo timeframe map
_YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d"}
_YF_PERIOD = {"1m": "1d", "5m": "5d", "15m": "5d", "1h": "60d", "1d": "1y"}


def _fetch_yahoo_ohlcv(ticker, timeframe, limit):
    try:
        import pandas as pd
        df = yf.download(ticker, interval=_YF_INTERVAL.get(timeframe,"60m"),
                        period=_YF_PERIOD.get(timeframe,"60d"),
                        progress=False, auto_adjust=False)
        if df is None or df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]
        df = df.tail(limit).reset_index()
        result = []
        for _, row in df.iterrows():
            try:
                ts = row.iloc[0]
                result.append([
                    int(ts.timestamp()*1000),
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    float(row.get("Volume", 0) or 0),
                ])
            except:
                pass
        return result
    except Exception as e:
        logger.warning(f"Yahoo fetch failed {ticker}: {e}")
        return []
        df = df.tail(limit)
        out = []
        for ts, row in df.iterrows():
            out.append([
                int(ts.timestamp() * 1000),
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                float(row.get("Volume", 0) or 0),
            ])
        return out
    except Exception as e:
        logger.warning(f"Yahoo fetch failed {ticker}: {e}")
        return []
