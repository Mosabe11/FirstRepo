"""
bots/levels_bot.py — Continuously computes & maintains S/R levels for every asset.

Runs every LEVELS_INTERVAL seconds (default 1800 = 30 min).
Stores results in core.levels._cache and broadcasts updates via Telegram.
"""
import logging
import threading

from config import settings
from core import market_data
from core.levels import compute_levels
from core.notify import tg_send
from storage.watchlist_runtime import registry

logger = logging.getLogger(__name__)


class LevelsBot:
    name = "levels"

    def __init__(self):
        self._stop = threading.Event()
        self._last_levels = {}  # asset -> previous levels for change detection

    def stop(self):
        self._stop.set()

    def run(self):
        interval = getattr(settings, "LEVELS_INTERVAL", 1800)
        logger.info(f"Levels bot started (refresh every {interval}s)")
        # ابدأ بعد دقيقة عشان باقي الـ bots يستقرّوا
        self._stop.wait(60)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception(f"Levels tick error: {e}")
            self._stop.wait(interval)
        logger.info("Levels bot stopped")

    def _tick(self):
        assets = registry.all()
        updated = 0
        new_levels_summary = []

        for cfg in assets:
            if self._stop.is_set():
                return
            try:
                # Force recompute by clearing cache for this asset
                from core.levels import _cache, _cache_lock
                cache_key = f"levels:{cfg.asset_class}:{cfg.exchange_symbol}"
                with _cache_lock:
                    _cache.pop(cache_key, None)

                cur_price = market_data.fetch_price(cfg.asset_class, cfg.exchange_symbol)
                if not cur_price:
                    continue
                levels = compute_levels(
                    cfg.asset_class, cfg.exchange_symbol,
                    market_data.fetch_ohlcv, cur_price,
                )
                if not levels:
                    continue

                updated += 1
                # Check for major change
                prev = self._last_levels.get(cfg.symbol)
                if prev:
                    if self._levels_changed_significantly(prev, levels):
                        new_levels_summary.append((cfg.symbol, levels, cur_price))
                self._last_levels[cfg.symbol] = levels

            except Exception as e:
                logger.warning(f"Levels failed {cfg.symbol}: {e}")

        logger.info(f"Levels bot: refreshed {updated} assets")

        # Send digest كل دورة (مش لكل asset)
        if updated > 0:
            self._send_digest()

    def _levels_changed_significantly(self, prev, current):
        """تحقق إذا S/R تغيرت بشكل ملحوظ."""
        try:
            old_sup = [s[0] for s in prev.get("support", [])]
            new_sup = [s[0] for s in current.get("support", [])]
            old_res = [r[0] for r in prev.get("resistance", [])]
            new_res = [r[0] for r in current.get("resistance", [])]

            if len(old_sup) != len(new_sup) or len(old_res) != len(new_res):
                return True
            for o, n in zip(old_sup + old_res, new_sup + new_res):
                if abs(n - o) / o > 0.01:  # 1%+ change
                    return True
            return False
        except Exception:
            return False

    def _send_digest(self):
        """يبعت ملخص للـ S/R levels لأهم العملات."""
        lines = ["🎯 *S/R Levels Update*"]
        priority = ["BTC", "ETH", "SOL", "GOLD", "EURUSD"]

        for sym in priority:
            lv = self._last_levels.get(sym)
            if not lv:
                continue
            cp = lv["current_price"]
            sup_str = ", ".join(f"{s[0]:.2f}" for s in lv["support"][:2]) or "—"
            res_str = ", ".join(f"{r[0]:.2f}" for r in lv["resistance"][:2]) or "—"
            lines.append(f"`{sym}` @ {cp:.2f}")
            lines.append(f"  R: {res_str}")
            lines.append(f"  S: {sup_str}")

        tg_send("\n".join(lines), category="general")
