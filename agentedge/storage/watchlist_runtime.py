"""
storage/watchlist_runtime.py
----------------------------
Runtime watchlist: base assets + discovered ones. Thread-safe.
Discovery bot adds, Cleaner bot removes.
"""

from __future__ import annotations
import threading

from config.watchlist import BASE_WATCHLIST, AssetConfig


class WatchlistRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._assets: dict[str, AssetConfig] = {}
        for a in BASE_WATCHLIST:
            self._assets[a.symbol] = a

    def all(self) -> list[AssetConfig]:
        with self._lock:
            return list(self._assets.values())

    def crypto_only(self) -> list[AssetConfig]:
        with self._lock:
            return [a for a in self._assets.values() if a.asset_class == "crypto"]

    def metals_only(self) -> list[AssetConfig]:
        with self._lock:
            return [a for a in self._assets.values() if a.asset_class == "metal"]

    def get(self, symbol: str) -> AssetConfig | None:
        with self._lock:
            return self._assets.get(symbol)

    def add(self, asset: AssetConfig) -> bool:
        with self._lock:
            if asset.symbol in self._assets:
                return False
            self._assets[asset.symbol] = asset
            return True

    def remove(self, symbol: str) -> bool:
        with self._lock:
            cfg = self._assets.get(symbol)
            if cfg is None or cfg.is_base:
                return False
            del self._assets[symbol]
            return True

    def size(self) -> int:
        with self._lock:
            return len(self._assets)


registry = WatchlistRegistry()
