"""
config/watchlist.py — Base watchlist assets (always monitored).
"""
from dataclasses import dataclass


@dataclass
class AssetConfig:
    symbol: str               # internal name e.g. "BTC"
    exchange_symbol: str      # how the exchange knows it e.g. "BTC/USDT" or "GLD"
    asset_class: str          # "crypto" | "binance" | "metal" | "forex"
    base_qty: float
    tp_pct: float = 0.015
    sl_pct: float = 0.008
    is_base: bool = True


BASE_WATCHLIST: list[AssetConfig] = [
    # ===== Crypto (MEXC) =====
    AssetConfig("BTC",    "BTC/USDT", "crypto", base_qty=0.005),
    AssetConfig("ETH",    "ETH/USDT", "crypto", base_qty=0.5),
    AssetConfig("XRP",    "XRP/USDT", "crypto", base_qty=250),
    AssetConfig("SOL",    "SOL/USDT", "crypto", base_qty=2.5),

    # ===== Metals (Yahoo) =====
    AssetConfig("GOLD",   "GLD",      "metal",   base_qty=0.5,  tp_pct=0.008, sl_pct=0.004),
    AssetConfig("SILVER", "SLV",      "metal",   base_qty=5.0,  tp_pct=0.012, sl_pct=0.006),

    # ===== Forex (Yahoo) =====
    AssetConfig("EURUSD", "EURUSD=X", "forex",   base_qty=200,  tp_pct=0.006, sl_pct=0.003),
    AssetConfig("GBPUSD", "GBPUSD=X", "forex",   base_qty=200,  tp_pct=0.006, sl_pct=0.003),
    AssetConfig("USDJPY", "USDJPY=X", "forex",   base_qty=2,  tp_pct=0.006, sl_pct=0.003),
    AssetConfig("USDCHF", "USDCHF=X", "forex",   base_qty=400,  tp_pct=0.006, sl_pct=0.003),
    AssetConfig("AUDUSD", "AUDUSD=X", "forex",   base_qty=400,  tp_pct=0.006, sl_pct=0.003),
    AssetConfig("USDCAD", "USDCAD=X", "forex",   base_qty=200,  tp_pct=0.006, sl_pct=0.003),

    # ===== Binance alts =====
    AssetConfig("BNB",    "BNB/USDT", "binance", base_qty=0.5),
    AssetConfig("DOGE",   "DOGE/USDT","binance", base_qty=1000),
    AssetConfig("ADA",    "ADA/USDT", "binance", base_qty=250),
    AssetConfig("AVAX",   "AVAX/USDT","binance", base_qty=10),
]


def to_dict() -> dict[str, AssetConfig]:
    return {a.symbol: a for a in BASE_WATCHLIST}
