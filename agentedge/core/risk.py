"""
core/risk.py
------------
Risk gatekeeper. Every trade goes through `pre_trade_check` before it can fire.

Tracks:
  - Open position count vs MAX_POSITIONS
  - Daily realized PnL vs DAILY_LIMIT
  - Weekly realized PnL vs WEEKLY_LIMIT
  - Per-asset cooldown windows
  - Trades-per-hour rate cap

Auto-resets daily counters at midnight UTC and weekly at start of week (Mon).
"""

from __future__ import annotations
import time
import threading
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

from config import settings


@dataclass
class RiskState:
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    daily_anchor: float = field(default_factory=lambda: _start_of_today_utc())
    weekly_anchor: float = field(default_factory=lambda: _start_of_week_utc())
    last_trade_ts_per_asset: dict[str, float] = field(default_factory=dict)
    last_trade_ts_global: list[float] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""


def _start_of_today_utc() -> float:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()


def _start_of_week_utc() -> float:
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    return datetime(monday.year, monday.month, monday.day,
                    tzinfo=timezone.utc).timestamp()


class RiskManager:
    def __init__(self):
        self.state = RiskState()
        self._lock = threading.Lock()

    # ---------- counter rollover ----------
    def _rollover(self):
        now = time.time()
        if now >= self.state.daily_anchor + 86400:
            self.state.daily_pnl = 0.0
            self.state.daily_anchor = _start_of_today_utc()
            self.state.blocked = False
            self.state.block_reason = ""
        if now >= self.state.weekly_anchor + 7 * 86400:
            self.state.weekly_pnl = 0.0
            self.state.weekly_anchor = _start_of_week_utc()

    # ---------- gatekeeping ----------
    def pre_trade_check(self, asset: str, open_positions: list,
                        has_position_on_asset: bool) -> tuple[bool, str]:
        with self._lock:
            self._rollover()

            if self.state.blocked:
                return False, f"Trading blocked: {self.state.block_reason}"

            if len(open_positions) >= settings.MAX_POSITIONS:
                return False, f"Max positions reached ({settings.MAX_POSITIONS})"

            if has_position_on_asset:
                return False, f"Already have a position on {asset}"

            if self.state.daily_pnl <= -abs(settings.DAILY_LIMIT):
                self.state.blocked = True
                self.state.block_reason = "daily drawdown limit hit"
                return False, self.state.block_reason

            if self.state.weekly_pnl <= -abs(settings.WEEKLY_LIMIT):
                self.state.blocked = True
                self.state.block_reason = "weekly drawdown limit hit"
                return False, self.state.block_reason

            # per-asset cooldown
            now = time.time()
            last = self.state.last_trade_ts_per_asset.get(asset, 0)
            if has_position_on_asset:
                cd = settings.TRIGGER_COOLDOWN_IF_OPEN
            else:
                cd = settings.TRIGGER_COOLDOWN_AFTER_TRADE
            if now - last < cd:
                return False, f"Cooldown on {asset} ({int(cd - (now - last))}s)"

            # trades-per-hour
            self.state.last_trade_ts_global = [
                ts for ts in self.state.last_trade_ts_global if now - ts < 3600
            ]
            if len(self.state.last_trade_ts_global) >= settings.MAX_TRADES_PER_HOUR:
                return False, "Hourly trade limit reached"

            return True, "OK"

    def record_trade_opened(self, asset: str):
        with self._lock:
            now = time.time()
            self.state.last_trade_ts_per_asset[asset] = now
            self.state.last_trade_ts_global.append(now)

    def record_trade_closed(self, pnl: float):
        with self._lock:
            self._rollover()
            self.state.daily_pnl += pnl
            self.state.weekly_pnl += pnl

    # ---------- sizing ----------
    def position_size(self, base_qty: float, edge: float,
                      asset_win_rate: float = 0.5) -> float:
        """
        Legacy quantity-based sizing (kept for callers that still pass base_qty).
        Prefer risk_based_size() — sizing by quantity makes dollar-risk uneven.
        """
        edge_factor = max(0.5, min(1.5, edge / 70.0))   # edge of 70 = 1.0x
        wr_factor = max(0.7, min(1.3, asset_win_rate / 0.5))
        return round(base_qty * edge_factor * wr_factor, 8)

    def risk_based_size(self, entry_price: float, stop_price: float,
                        equity: float, risk_fraction: float,
                        max_positions: int = 1) -> float:
        """
        Size so the loss at the stop equals `risk_fraction` of equity:
            qty = (equity * risk_fraction) / |entry - stop|
        Equalizes dollar-risk across assets of very different price/volatility.
        Capped so one position's notional can't exceed equity / max_positions
        (simple portfolio-heat guard). Returns 0.0 if inputs are unusable.
        """
        stop_dist = abs(entry_price - stop_price)
        if stop_dist <= 0 or entry_price <= 0 or equity <= 0:
            return 0.0
        qty = (equity * risk_fraction) / stop_dist
        # notional cap: total exposure across max_positions stays within equity
        notional_cap = equity / max(1, max_positions)
        qty_cap = notional_cap / entry_price
        return round(max(0.0, min(qty, qty_cap)), 8)

    # ---------- status ----------
    def snapshot(self) -> dict:
        with self._lock:
            self._rollover()
            return {
                "daily_pnl": round(self.state.daily_pnl, 2),
                "weekly_pnl": round(self.state.weekly_pnl, 2),
                "daily_limit": settings.DAILY_LIMIT,
                "weekly_limit": settings.WEEKLY_LIMIT,
                "blocked": self.state.blocked,
                "block_reason": self.state.block_reason,
                "trades_last_hour": len(self.state.last_trade_ts_global),
            }


# global singleton (one risk manager for the whole process)
risk_manager = RiskManager()
