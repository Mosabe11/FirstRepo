"""
bots/cleaner.py — Smart watchlist pruning.

Removes non-base assets that:
  - Have poor win rate (after 5+ trades)
  - No activity for 24+ hours
  - Total PnL very negative

Never touches:
  - Base assets
  - Assets with open positions
  - Assets with strong performance
"""
from __future__ import annotations
import time
import logging
import threading

from config import settings
from core.notify import tg_send
from execution.manager import position_manager
from storage.watchlist_runtime import registry

logger = logging.getLogger(__name__)


class CleanerBot:
    name = "cleaner"

    def __init__(self):
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        logger.info("Cleaner bot started")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception(f"Cleaner tick error: {e}")
            self._stop.wait(settings.CLEANER_INTERVAL)
        logger.info("Cleaner bot stopped")

    def _tick(self):
        non_base = [a for a in registry.all() if not a.is_base]
        if not non_base:
            return

        held = {p.asset for p in position_manager.open_positions()}
        candidates = [a for a in non_base if a.symbol not in held]
        if not candidates:
            return

        removed = []
        for asset_cfg in candidates:
            sym = asset_cfg.symbol
            reason = self._should_remove(sym)
            if reason:
                if registry.remove(sym):
                    removed.append((sym, reason))
                    logger.info(f"Cleaner removed {sym}: {reason}")

        if removed:
            lines = ["🗑 *Cleaner removed:*"]
            for sym, reason in removed:
                lines.append(f"  `{sym}` — {reason}")
            tg_send("\n".join(lines), category="cleaner")

    def _should_remove(self, symbol):
        """Returns reason string if asset should be removed, else None."""
        stats = position_manager._learning.get(symbol, {})
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = wins + losses

        # 1. Win rate كارثي بعد 5+ صفقات
        if total >= 5:
            wr = wins / total
            if wr < 0.30:
                return f"win rate {wr:.0%} ({wins}W/{losses}L)"

        # 2. خسائر متراكمة
        total_pnl = self._asset_pnl(symbol)
        if total >= 3 and total_pnl < -5:
            return f"losing PnL {total_pnl:+.2f} ({total} trades)"

        # 3. لا نشاط منذ 24 ساعة + 3+ trades بدون ربح
        last_trade_ts = self._last_trade_time(symbol)
        if last_trade_ts and time.time() - last_trade_ts > 86400:
            if total >= 3 and total_pnl <= 0:
                hours = (time.time() - last_trade_ts) / 3600
                return f"inactive {hours:.0f}h, PnL {total_pnl:+.2f}"

        return None

    def _asset_pnl(self, symbol):
        """Total PnL for this asset from trade history."""
        try:
            return sum(
                t.get("pnl", 0)
                for t in position_manager._trades
                if t.get("asset") == symbol
            )
        except Exception:
            return 0

    def _last_trade_time(self, symbol):
        """Timestamp of last trade on this asset."""
        try:
            relevant = [
                t.get("closed_at", 0)
                for t in position_manager._trades
                if t.get("asset") == symbol
            ]
            return max(relevant) if relevant else None
        except Exception:
            return None
