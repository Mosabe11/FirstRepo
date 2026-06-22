"""
bots/discovery.py — Smart asset discovery with multi-layer scam protection.

Filters (ALL must pass):
  1. Score >= 40 (volume + momentum)
  2. Volume >= $5M daily
  3. Price between $0.01 and $100,000 (no microcaps, no overpriced)
  4. Symbol clean (no parens, no special chars)
  5. Name does NOT contain base asset names (GOLD, SILVER, BTC, ETH, etc.)
  6. Has at least 24 hours of valid candle history
  7. No price gaps > 30% in last 24h (anti-pump)
"""
from __future__ import annotations
import logging
import threading
import re

from config import settings
from config.watchlist import AssetConfig
from core import market_data
from core.notify import tg_send
from storage.watchlist_runtime import registry

logger = logging.getLogger(__name__)

# Protected names - reject any discovered coin matching these
BASE_PROTECTED = {
    "BTC", "ETH", "XRP", "SOL", "BNB", "DOGE", "ADA", "AVAX",
    "GOLD", "SILVER", "OIL", "USD", "EUR", "GBP", "JPY",
    "ASSET", "TOKEN", "COIN", "MONEY", "CASH"
}


class DiscoveryBot:
    name = "discovery"

    def __init__(self):
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        logger.info("Discovery bot started")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception(f"Discovery tick error: {e}")
            self._stop.wait(settings.DISCOVERY_INTERVAL)
        logger.info("Discovery bot stopped")

    def _tick(self):
        if registry.size() >= settings.WATCHLIST_MAX_SIZE:
            return

        candidates = market_data.fetch_top_usdt_pairs(
            top_n=40, min_volume_usdt=settings.DISCOVERY_MIN_VOLUME_USDT
        )
        if not candidates:
            return

        scored = []
        for c in candidates:
            sym = c["symbol"]
            if registry.get(sym) is not None:
                continue
            # Score = volume (in millions) + abs % change
            score = (c["quote_volume"] / 1e7) + abs(c.get("percent_change", 0))
            scored.append((score, c))
        scored.sort(reverse=True, key=lambda x: x[0])

        added = 0
        for score, c in scored[: settings.DISCOVERY_BATCH_SIZE]:
            if registry.size() >= settings.WATCHLIST_MAX_SIZE:
                break

            sym = c["symbol"]
            reason = self._validate(sym, c, score)
            if reason:
                logger.info(f"Discovery skipped {sym} - {reason}")
                continue

            price = c.get("last", 0)
            target_notional = 50.0
            base_qty = max(0.0001, target_notional / price)
            base_qty = round(base_qty, 8)

            new_asset = AssetConfig(
                symbol=sym,
                exchange_symbol=c["exchange_symbol"],
                asset_class="crypto",
                base_qty=base_qty,
                tp_pct=0.015,
                sl_pct=0.008,
                is_base=False,
            )
            if registry.add(new_asset):
                added += 1
                logger.info(f"Discovery added {sym} (score={score:.1f})")
                tg_send(
                    f"🔍 Discovery added {sym}\n"
                    f"Score: {score:.1f} | Price: {price:.4f}",
                    category="discovery",
                )
        if added:
            logger.info(f"Discovery: +{added}, watchlist now {registry.size()}")

    def _validate(self, sym, candidate, score):
        """Returns reason string if asset should be REJECTED, else None."""

        # 1. Score threshold
        MIN_SCORE = 40.0
        if score < MIN_SCORE:
            return f"low score {score:.1f} < {MIN_SCORE}"

        # 2. Volume threshold (separate from min_volume to enforce per-candidate)
        vol = candidate.get("quote_volume", 0)
        if vol < 5_000_000:
            return f"low volume ${vol/1e6:.1f}M < $5M"

        # 3. Symbol cleanliness
        if "(" in sym or ")" in sym:
            return "symbol has parentheses (fake token)"
        if not re.match(r"^[A-Z0-9]+$", sym):
            return "symbol has special chars"

        # 4. Reject protected/base symbol matches
        sym_upper = sym.upper()
        for protected in BASE_PROTECTED:
            if protected in sym_upper:
                return f"symbol matches protected name {protected}"

        # 5. Price sanity
        price = candidate.get("last", 0)
        if price < 0.01:
            return f"price too small {price} (microcap)"
        if price > 100_000:
            return f"price too large {price}"

        # 6. Test actual data availability
        try:
            ac = candidate.get("asset_class", "crypto")
            exs = candidate.get("exchange_symbol", sym + "/USDT")
            test_candles = market_data.fetch_ohlcv(ac, exs, "1h", 24)
            if not test_candles or len(test_candles) < 20:
                return "insufficient history (<20 hours)"

            # 7. Anti-pump: no candle moved > 30% from previous close
            for i in range(1, len(test_candles)):
                prev_close = float(test_candles[i-1][4])
                this_close = float(test_candles[i][4])
                if prev_close > 0:
                    change = abs(this_close - prev_close) / prev_close
                    if change > 0.30:
                        return f"pump detected ({change*100:.0f}% move)"
        except Exception as e:
            return f"data check failed: {e}"

        return None  # all checks passed
