"""
bots/monitor.py
---------------
Watches open positions every few seconds.
Pulls fresh prices, lets each Position update its state machine,
closes anything that hits TP / SL / trailing.
"""

from __future__ import annotations
import time
import logging
import threading

from config import settings
from core import market_data
from execution.manager import position_manager
from storage.watchlist_runtime import registry

logger = logging.getLogger(__name__)


class MonitorBot:
    name = "monitor"

    def __init__(self):
        self._stop = threading.Event()
        self._last_pulse = 0
        self._last_prices = {}      # asset -> last price (for breakout detection)
        self._last_breakout = {}    # asset -> timestamp (cooldown 30 min)

    def stop(self):
        self._stop.set()

    def run(self):
        logger.info("Monitor bot started")
        while not self._stop.is_set():
            try:
                self._tick()
                self._maybe_pulse()
            except Exception as e:
                logger.exception(f"Monitor tick error: {e}")
            self._stop.wait(settings.MONITOR_INTERVAL)
        logger.info("Monitor bot stopped")

    def _tick(self):
        positions = position_manager.open_positions()
        if not positions:
            return
        # bucket prices to avoid duplicate fetches
        seen_prices: dict[str, float | None] = {}
        for pos in positions:
            cfg = registry.get(pos.asset)
            if not cfg:
                continue
            key = (cfg.asset_class, cfg.exchange_symbol)
            if key in seen_prices:
                price = seen_prices[key]
            else:
                price = market_data.fetch_price(cfg.asset_class, cfg.exchange_symbol)
                seen_prices[key] = price
            if price is None:
                continue
            outcome = pos.update(price)
            if outcome == "HALF":
                # Partial close: اقفل 50% واحفظ الـ position
                try:
                    position_manager.half_close_position(pos.id)
                except Exception as e:
                    logger.warning(f"Half close failed: {e}")
            elif outcome != "HOLD":
                position_manager.close_position(pos.id, reason=outcome)

        # === Breakout Detection (no-position assets) ===
        try:
            self._check_breakouts(seen_prices)
        except Exception as e:
            logger.warning(f"Breakout check failed: {e}")

    def _check_breakouts(self, seen_prices):
        """يفحص breakouts على S/R لكل asset (مش بس المفتوحة)."""
        import time as _time
        from core.levels import detect_breakout
        from core import market_data
        from core.notify import tg_send
        from execution.manager import position_manager

        for cfg in registry.all():
            asset = cfg.symbol
            # cooldown 30 دقيقة
            if _time.time() - self._last_breakout.get(asset, 0) < 1800:
                continue
            # لا تتحقق إذا في صفقة مفتوحة على نفس الأصل
            if position_manager.has_position_on(asset):
                continue

            key = (cfg.asset_class, cfg.exchange_symbol)
            cur_price = seen_prices.get(key)
            if cur_price is None:
                cur_price = market_data.fetch_price(cfg.asset_class, cfg.exchange_symbol)
                if cur_price:
                    seen_prices[key] = cur_price
            if not cur_price:
                continue

            prev = self._last_prices.get(asset)
            self._last_prices[asset] = cur_price
            if prev is None or abs(cur_price - prev) / prev < 0.001:
                continue  # حركة ضعيفة، تجاهل

            event, level = detect_breakout(
                cfg.asset_class, cfg.exchange_symbol,
                market_data.fetch_ohlcv, cur_price, prev,
            )
            if not event:
                continue

            self._last_breakout[asset] = _time.time()
            emoji = "🟢" if event in ("RESISTANCE_BREAK", "SUPPORT_BOUNCE") else "🔴"
            tg_send(
                f"{emoji} Breakout {asset} {event}\nLevel: {level:.4f} | Price: {cur_price:.4f}",
                category="signal",
            )
            logger.info(f"Breakout {asset} {event} @ {level:.4f} (price {cur_price:.4f})")

            # === Ask AI Breakout Trader ===
            try:
                from council.breakout_trader import decide_breakout
                from core.signal import Signal
                from config.watchlist import to_dict
                decision = decide_breakout(
                    asset, cfg.asset_class, event, level, cur_price,
                    market_data.fetch_ohlcv,
                )
                if not decision.get("should_trade"):
                    tg_send(
                        f"🤖 AI declined {asset} breakout\nReason: {decision.get('reasoning','')[:120]}",
                        category="ai",
                    )
                    continue

                direction = decision.get("direction", "LONG")
                conf = decision.get("confidence", 0)
                if conf < 60:
                    tg_send(
                        f"🤖 AI low confidence {asset} ({conf}%) — skipped",
                        category="ai",
                    )
                    continue

                sig = Signal(
                    asset=asset, direction=direction, edge=min(95, 60 + conf*0.3),
                    price=cur_price, strategy="breakout_ai",
                    timeframe="5m",
                    reason=f"{event} @ {level:.4f}: {decision.get('reasoning','')[:80]}",
                    stop_loss=decision["suggested_sl"],
                    take_profit=decision["suggested_tp"],
                    extras={"event": event, "level": level},
                )
                ok, msg = position_manager.try_open(sig, cfg, use_ai=False)
                tg_send(
                    f"🤖 AI Breakout {asset} {direction} ({conf}%): {msg}",
                    category="ai",
                )
            except Exception as e:
                logger.warning(f"Breakout AI error: {e}")

    def _maybe_pulse(self):
        """Send a Market Pulse every PULSE_INTERVAL seconds."""
        interval = getattr(settings, "PULSE_INTERVAL", 300)
        if time.time() - self._last_pulse < interval:
            return
        self._last_pulse = time.time()
        try:
            from core.notify import tg_send
            from execution.manager import position_manager
            stats = position_manager.stats()
            positions = position_manager.open_positions()
            watchlist_count = len(registry.all())

            lines = [
                "📊 *Market Pulse*",
                f"Mode: `{stats['mode']}` | Watchlist: {watchlist_count} assets",
                f"Open: {len(positions)} positions | PnL: {stats['total_pnl']:+.2f}",
                f"Win rate: {stats['win_rate']}% ({stats['wins']}W / {stats['losses']}L)",
            ]

            if positions:
                lines.append("\n*Open:*")
                for p in positions[:5]:
                    cfg = registry.get(p.asset)
                    if cfg:
                        cur = market_data.fetch_price(cfg.asset_class, cfg.exchange_symbol) or p.entry_price
                        pnl = p.pnl(cur)
                        emoji = "🟢" if pnl >= 0 else "🔴"
                        lines.append(f"{emoji} `{p.asset}` {p.direction} pnl={pnl:+.4f}")

            tg_send("\n".join(lines), category="pulse")
        except Exception as e:
            logger.warning(f"Pulse failed: {e}")
