"""
research/data.py — real OHLCV loader (ccxt / binance public, no API keys).

Paginates backwards to assemble a long continuous history and caches it to
CSV under research/cache/ so re-runs are instant and reproducible. Candle
format matches the rest of the codebase: [ts_ms, open, high, low, close, vol].
"""
from __future__ import annotations
import csv
import time
from pathlib import Path

import ccxt

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
          "4h": 14_400_000, "1d": 86_400_000}


def _cache_path(symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "-")
    return CACHE_DIR / f"{safe}_{timeframe}.csv"


def load(symbol: str = "BTC/USDT", timeframe: str = "1h",
         bars: int = 4000, force: bool = False) -> list[list]:
    """Return up to `bars` most-recent candles, cached on disk."""
    path = _cache_path(symbol, timeframe)
    if path.exists() and not force:
        rows = []
        with open(path, newline="") as f:
            for r in csv.reader(f):
                rows.append([float(r[0])] + [float(x) for x in r[1:]])
        if len(rows) >= bars:
            return rows[-bars:]

    ex = ccxt.binance({"enableRateLimit": True, "timeout": 20000})
    step = _TF_MS[timeframe]
    per_call = 1000
    end = ex.milliseconds()
    since = end - bars * step
    out: list[list] = []
    cursor = since
    while cursor < end and len(out) < bars + per_call:
        try:
            batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=per_call)
        except Exception as e:
            print(f"  fetch error {symbol} {timeframe}: {e}; retrying once")
            time.sleep(1.0)
            batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=per_call)
        if not batch:
            break
        out.extend(batch)
        cursor = batch[-1][0] + step
        if len(batch) < per_call:
            break

    # de-dup + sort
    seen, clean = set(), []
    for c in sorted(out, key=lambda x: x[0]):
        if c[0] in seen:
            continue
        seen.add(c[0])
        clean.append([float(c[0])] + [float(x) for x in c[1:6]])

    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(clean)
    return clean[-bars:]


if __name__ == "__main__":
    for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        c = load(sym, "1h", 4000, force=True)
        print(f"{sym}: {len(c)} candles, "
              f"{time.strftime('%Y-%m-%d', time.gmtime(c[0][0]/1000))} -> "
              f"{time.strftime('%Y-%m-%d', time.gmtime(c[-1][0]/1000))}")
