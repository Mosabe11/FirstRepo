"""
execution/manager.py — v3 with Council, Memory, News, Adaptive weights.
"""
import time
import logging
import threading

from config import settings, watchlist
from core.signal import Signal
from core.risk import risk_manager
from core import market_data
from core import ai_confirm
from core.notify import tg_send
from core.indicators import compute_all
from execution import router
from execution.position import Position
from storage import state as state_store

logger = logging.getLogger(__name__)


class PositionManager:
    def __init__(self):
        self._positions = {}
        self._close_failures = {}  # position_id -> failure count
        self._macro_block_last_sent = {}  # asset -> timestamp
        self._ai_veto_last_sent = {}  # (asset, direction) -> timestamp
        self._lock = threading.RLock()
        self._trades = []
        self._learning = {}
        self._load()

    def _load(self):
        st = state_store.load_state()
        for p in st.get("positions", []):
            try:
                pos = Position.from_dict(p)
                self._positions[pos.id] = pos
            except Exception as e:
                logger.warning(f"Skipped malformed position: {e}")
        self._trades = st.get("trades", [])
        self._learning = st.get("learning", {})
        logger.info(f"Loaded {len(self._positions)} positions, {len(self._trades)} historical trades")
        # تنظيف الأشباح تلقائياً: صفقات مفتوحة بالسجل وغير محمّلة كمراكز حيّة
        try:
            from memory import persistent as _persistent
            _n = _persistent.reconcile_ghosts(list(self._positions.keys()))
            if _n:
                logger.warning(f"reconcile_ghosts: نظّفت {_n} صفقة شبح عند الإقلاع")
        except Exception as _e:
            logger.error(f"reconcile_ghosts فشل: {_e}")

    def _save(self):
        state_store.save_state({
            "positions": [p.to_dict() for p in self._positions.values()],
            "trades": self._trades[-500:],
            "learning": self._learning,
        })

    def open_positions(self):
        with self._lock:
            return list(self._positions.values())

    def has_position_on(self, asset):
        with self._lock:
            return any(p.asset == asset for p in self._positions.values())

    def asset_win_rate(self, asset):
        stats = self._learning.get(asset, {})
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        total = wins + losses
        return wins / total if total >= 5 else 0.5

    def stats(self):
        with self._lock:
            wins = sum(1 for t in self._trades if t.get("pnl", 0) > 0)
            losses = sum(1 for t in self._trades if t.get("pnl", 0) <= 0)
            total_pnl = sum(t.get("pnl", 0) for t in self._trades)
            total = wins + losses
            return {
                "open_positions": len(self._positions),
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / total * 100, 1) if total else 0,
                "total_pnl": round(total_pnl, 2),
                "mode": router.mode_label(),
            }

    def recent_trades(self, n=20):
        with self._lock:
            return self._trades[-n:][::-1]

    def try_open(self, signal, asset_cfg, use_ai=True):
        if not signal.is_actionable:
            return False, "signal not actionable"

        with self._lock:
            ok, reason = risk_manager.pre_trade_check(
                signal.asset, list(self._positions.values()),
                self.has_position_on(signal.asset),
            )
        if not ok:
            return False, reason

        current_price = market_data.fetch_price(asset_cfg.asset_class, asset_cfg.exchange_symbol)
        if current_price is None:
            return False, "could not fetch current price"
        drift = abs(current_price - signal.price) / signal.price
        if drift > settings.MAX_PRICE_DRIFT:
            return False, f"price drifted {drift*100:.2f}%"


        # === AI Strategist Gate (نهائي قبل كل صفقة) ===
        # تجاوز هذا الفلتر للـ AI agents (هم أصلاً AI)
        if signal.strategy not in ("ai_strategist", "breakout_ai") and use_ai:
            try:
                from council.ai_strategist import generate_signal as _ai_check
                ai_verdict = _ai_check(signal.asset, asset_cfg.asset_class, market_data.fetch_ohlcv)
                if ai_verdict:
                    should = ai_verdict.get("should_trade", False)
                    ai_dir = ai_verdict.get("direction", "").upper()
                    ai_edge = ai_verdict.get("edge", 0)
                    # رفض إذا AI قال لا أو اتجاه معاكس
                    if not should:
                        reason_txt = ai_verdict.get("reasoning", "")[:80]
                        # ابعت تنبيه واحد فقط لكل (asset, direction) كل 15 دقيقة
                        import time as _time
                        key = (signal.asset, signal.direction)
                        if _time.time() - self._ai_veto_last_sent.get(key, 0) > 900:
                            from core.notify import tg_send as _tg
                            _tg(
                                f"🚫 AI Veto {signal.asset} {signal.direction}\n"
                                f"Strategy: {signal.strategy} (edge={signal.edge:.0f})\n"
                                f"AI: {reason_txt}",
                                category="ai",
                            )
                            self._ai_veto_last_sent[key] = _time.time()
                        return False, f"AI veto: {reason_txt[:60]}"
                    if ai_dir and ai_dir != signal.direction:
                        import time as _time
                        key = (signal.asset, signal.direction, "conflict")
                        if _time.time() - self._ai_veto_last_sent.get(key, 0) > 900:
                            from core.notify import tg_send as _tg
                            _tg(
                                f"🚫 AI Direction Conflict {signal.asset}\n"
                                f"Strategy says {signal.direction}, AI says {ai_dir}",
                                category="ai",
                            )
                            self._ai_veto_last_sent[key] = _time.time()
                        return False, f"AI direction conflict ({ai_dir} vs {signal.direction})"
            except Exception as e:
                logger.warning(f"AI Strategist gate error: {e}")

        # === News block (Phase 3) ===
        if settings.ENABLE_NEWS_FILTER:
            try:
                from news.sentiment import is_blocking_news
                blocked, news_reason = is_blocking_news(
                    signal.asset, asset_cfg.asset_class, signal.direction
                )
                if blocked:
                    return False, f"news: {news_reason}"
            except Exception as e:
                logger.warning(f"News filter error: {e}")

        # === Build context — use signal's own indicators + 1h for regime ===
        candles = market_data.fetch_ohlcv(asset_cfg.asset_class, asset_cfg.exchange_symbol, "1h", 50)
        indicators = compute_all(candles) if candles else {}
        # Merge signal extras (actual indicators at signal time) into indicators
        if signal.extras:
            indicators.update(signal.extras)

        # === DECISION via Council (Phase 1) ===
        decision = None
        confidence = 0.0
        council_used = False

        if use_ai and settings.ENABLE_COUNCIL and settings.DEEPSEEK_KEY and signal.edge >= 75:
            try:
                from council.council import deliberate
                memory_ctx = ""
                if settings.ENABLE_MEMORY:
                    from memory.persistent import build_context_for_council
                    memory_ctx = build_context_for_council(signal.asset, signal.direction, signal.strategy)
                decision, confidence, votes = deliberate(
                    signal.asset, signal.direction, current_price,
                    indicators, candles, risk_manager.snapshot(),
                    memory_context=memory_ctx,
                )
                council_used = True
            except Exception as e:
                logger.warning(f"Council error, falling back to simple AI: {e}")

        if decision is None and use_ai and settings.DEEPSEEK_KEY:
            decision, confidence = ai_confirm.confirm_trade(
                signal.asset, signal.direction, current_price, signal.extras or {}
            )

        if decision is not None:
            wanted = "BUY" if signal.direction == "LONG" else "SELL"
            if decision != wanted:
                return False, f"{'Council' if council_used else 'AI'} says {decision} (conf {confidence:.0f})"
            if confidence < settings.AI_MIN_CONFIDENCE:
                return False, f"{'Council' if council_used else 'AI'} confidence {confidence:.0f} < threshold"

        # === Sizing with adaptive weight (Phase 4) ===
        size_modifier = 1.0
        if settings.ENABLE_ADAPTIVE_WEIGHTS:
            try:
                from backtest.engine import get_strategy_weight
                size_modifier = get_strategy_weight(signal.asset, signal.strategy)
            except Exception:
                pass

        qty = risk_manager.position_size(
            asset_cfg.base_qty, signal.edge, self.asset_win_rate(signal.asset)
        ) * size_modifier
        qty = round(qty, 8)

        # === Execute ===
        result = router.submit_order(
            asset_cfg.asset_class, asset_cfg.exchange_symbol,
            signal.direction, qty, current_price,
        )
        if not result["filled"]:
            return False, f"execution failed: {result.get('error')}"

        entry = result["fill_price"]
        if signal.stop_loss and signal.take_profit:
            sl, tp = signal.stop_loss, signal.take_profit
        else:
            if signal.direction == "LONG":
                sl = entry * (1 - asset_cfg.sl_pct)
                tp = entry * (1 + asset_cfg.tp_pct)
            else:
                sl = entry * (1 + asset_cfg.sl_pct)
                tp = entry * (1 - asset_cfg.tp_pct)

        pos = Position.new(
            asset=signal.asset, direction=signal.direction,
            entry=entry, qty=qty, sl=sl, tp=tp,
            strategy=signal.strategy, paper=not router.is_live(),
        )

        with self._lock:
            self._positions[pos.id] = pos
            self._save()
        risk_manager.record_trade_opened(signal.asset)
        logger.info(f"OPENED {pos.direction} {pos.asset} qty={pos.quantity} entry={pos.entry_price:.6f}")

        # === Persistent memory record (Phase 2) ===
        if settings.ENABLE_MEMORY:
            try:
                from memory.persistent import record_trade_open
                rsi_val = macd_val = atr_val = None
                try:
                    if indicators:
                        rsi_val = float(indicators["rsi"][-1])
                        macd_val = float(indicators["macd_hist"][-1])
                        atr_val = float(indicators["atr"][-1])
                except Exception:
                    pass
                record_trade_open(
                    trade_id=pos.id, asset=pos.asset, direction=pos.direction,
                    strategy=pos.strategy, entry_price=entry, quantity=qty,
                    entry_rsi=rsi_val, entry_macd_hist=macd_val, entry_atr=atr_val,
                    council_decision=decision, council_confidence=confidence,
                    signal_edge=signal.edge,
                )
            except Exception as e:
                logger.warning(f"Memory record_open failed: {e}")

        decision_label = "Council" if council_used else ("AI" if use_ai else "Direct")
        tg_send(
            f"✅ *OPENED* {pos.direction} `{pos.asset}`\n"
            f"Entry: {pos.entry_price:.4f}\n"
            f"Qty: {pos.quantity} (×{size_modifier:.2f})\n"
            f"SL: {pos.stop_loss:.4f}\n"
            f"TP: {pos.take_profit:.4f}\n"
            f"Strategy: {pos.strategy}\n"
            f"Edge: {signal.edge:.0f}\n"
            f"Decision by: {decision_label} ({confidence:.0f}%)\n"
            f"Reason: {signal.reason}",
            category="open_close",
        )
        return True, f"opened {pos.id}"

    def half_close_position(self, position_id):
        """يقفل 50% من الصفقة ويترك النص الباقي يكمل مع trailing."""
        with self._lock:
            pos = self._positions.get(position_id)
            if not pos:
                return False, "position not found"
            if pos.half_closed:
                return False, "already half-closed"

        cfg = watchlist.to_dict().get(pos.asset)
        if not cfg:
            asset_class, exchange_symbol = "crypto", f"{pos.asset}/USDT"
        else:
            asset_class = cfg.asset_class
            exchange_symbol = cfg.exchange_symbol

        current_price = market_data.fetch_price(asset_class, exchange_symbol)
        if current_price is None:
            return False, "could not fetch current price"

        # احسب نصف الكمية
        half_qty = pos.quantity / 2
        result = router.close_order(asset_class, exchange_symbol, pos.direction, half_qty, current_price)
        if not result["filled"]:
            return False, f"half close failed: {result.get('error')}"

        exit_price = result["fill_price"]
        # احسب الـ partial PnL
        if pos.direction == "LONG":
            partial_pnl = (exit_price - pos.entry_price) * half_qty
        else:
            partial_pnl = (pos.entry_price - exit_price) * half_qty
        partial_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        if pos.direction == "SHORT":
            partial_pct = -partial_pct

        with self._lock:
            # حدّث الـ position بالنص الباقي
            pos.quantity = pos.quantity - half_qty
            pos.half_closed = True
            self._save()

        logger.info(f"HALF-CLOSED {pos.direction} {pos.asset} qty={half_qty:.4f} exit={exit_price:.4f} pnl={partial_pnl:.2f}")
        tg_send(
            f"💰 Half-Close {pos.asset} {pos.direction}\n"
            f"Closed 50% @ {exit_price:.4f}\n"
            f"Partial PnL: {partial_pnl:+.2f} ({partial_pct:+.2f}%)\n"
            f"Remaining 50% trailing...",
            category="open_close",
        )
        return True, "half-closed"

    def close_position(self, position_id, reason):
        with self._lock:
            pos = self._positions.get(position_id)
            if not pos:
                return False, "position not found"

        cfg = watchlist.to_dict().get(pos.asset)
        if not cfg:
            asset_class, exchange_symbol = "crypto", f"{pos.asset}/USDT"
        else:
            asset_class = cfg.asset_class
            exchange_symbol = cfg.exchange_symbol

        current_price = market_data.fetch_price(asset_class, exchange_symbol)
        if current_price is None:
            return False, "could not fetch current price"

        result = router.close_order(asset_class, exchange_symbol, pos.direction, pos.quantity, current_price)
        if not result["filled"]:
            return False, f"close failed: {result.get('error')}"

        exit_price = result["fill_price"]
        # Sanity check: reject extreme price moves (data error)
        price_change = abs(exit_price - pos.entry_price) / pos.entry_price
        if price_change > 0.5:
            # Count failures
            self._close_failures[position_id] = self._close_failures.get(position_id, 0) + 1
            fail_count = self._close_failures[position_id]
            if fail_count <= 3:
                logger.warning(f"REJECTED close {pos.asset}: extreme move {price_change*100:.0f}% (attempt {fail_count}/5)")
                if fail_count == 1:
                    tg_send(f"⚠️ Data error on {pos.asset} (move {price_change*100:.0f}%) — will auto-purge after 5 failures", category="general")
                return False, f"data error: {price_change*100:.0f}% move suspicious"
            # بعد 5 محاولات، احذفها قسراً بدون closing
            logger.warning(f"FORCE-REMOVING {pos.asset} after {fail_count} failed closes")
            with self._lock:
                self._positions.pop(position_id, None)
                self._close_failures.pop(position_id, None)
                self._save()
            tg_send(f"🗑 *Force-removed* `{pos.asset}` — persistent data error", category="general")
            return True, "force-removed due to data errors"
        pnl = pos.pnl(exit_price)
        pnl_pct = pos.pnl_pct(exit_price)

        with self._lock:
            self._positions.pop(position_id, None)
            self._trades.append({
                "id": pos.id, "asset": pos.asset, "direction": pos.direction,
                "strategy": pos.strategy, "entry": pos.entry_price, "exit": exit_price,
                "qty": pos.quantity, "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct * 100, 3),
                "reason": reason, "opened_at": pos.opened_at,
                "closed_at": time.time(),
                "mode": "live" if not pos.paper else "paper",
            })
            stats = self._learning.setdefault(pos.asset, {"wins": 0, "losses": 0, "by_strategy": {}})
            if pnl > 0:
                stats["wins"] += 1
            else:
                stats["losses"] += 1
            ss = stats["by_strategy"].setdefault(pos.strategy, {"wins": 0, "losses": 0})
            if pnl > 0:
                ss["wins"] += 1
            else:
                ss["losses"] += 1
            self._save()

        risk_manager.record_trade_closed(pnl)
        logger.info(f"CLOSED {pos.direction} {pos.asset} exit={exit_price:.6f} pnl={pnl:+.4f}")

        if settings.ENABLE_MEMORY:
            try:
                from memory.persistent import record_trade_close
                record_trade_close(pos.id, exit_price, pnl, pnl_pct, reason)
            except Exception as e:
                logger.warning(f"Memory record_close failed: {e}")

        emoji = "🟢" if pnl > 0 else "🔴"
        tg_send(
            f"{emoji} *CLOSED* {pos.direction} `{pos.asset}`\n"
            f"Exit: {exit_price:.4f}\n"
            f"PnL: {pnl:+.4f} ({pnl_pct*100:+.2f}%)\n"
            f"Reason: {reason}\n"
            f"Strategy: {pos.strategy}",
            category="open_close",
        )
        return True, f"closed pnl={pnl:+.4f}"

    def close_all(self, reason="manual close_all"):
        results = []
        with self._lock:
            ids = list(self._positions.keys())
        for pid in ids:
            ok, msg = self.close_position(pid, reason)
            results.append((pid, ok, msg))
        return results

    def reset_paper(self):
        if router.is_live():
            return False
        with self._lock:
            self._positions.clear()
            self._trades.clear()
            self._learning.clear()
            self._save()
        return True


position_manager = PositionManager()
