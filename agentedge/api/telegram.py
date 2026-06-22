"""
api/telegram.py
---------------
Polls Telegram for commands and sends notifications.
Only responds to messages from the configured TELEGRAM_CHAT_ID.

Commands:
  /status          PnL, open positions, win rate
  /watchlist       list tracked assets
  /closeall        close every open position
  /close <ASSET>   close one position by asset symbol
  /signal A LONG   force a manual trade
  /reset           wipe paper stats (paper mode only)
  STOP             graceful shutdown signal
  <anything>       quick analysis of that asset
"""

from __future__ import annotations
import time
import logging
import threading
import requests

from config import settings
from config.watchlist import AssetConfig
from core.signal import Signal
from execution.manager import position_manager
from execution import router
from core.risk import risk_manager
from core import market_data
from core.indicators import compute_all
from storage.watchlist_runtime import registry

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot"


class TelegramBot:
    name = "telegram"

    def __init__(self, shutdown_event: threading.Event):
        self._stop = threading.Event()
        self._shutdown = shutdown_event
        self._offset = 0

    def stop(self):
        self._stop.set()

    def enabled(self) -> bool:
        return bool(settings.TELEGRAM_TOKEN and settings.TELEGRAM_CHAT_ID)

    # ---------- notifications ----------
    def send(self, text: str):
        if not self.enabled():
            return
        try:
            requests.post(
                f"{_API}{settings.TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text,
                      "parse_mode": "Markdown"},
                timeout=8,
            )
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    # ---------- listener loop ----------
    def run(self):
        if not self.enabled():
            logger.info("Telegram not configured — listener disabled")
            return
        logger.info("Telegram listener started")
        self.send(f"🤖 AgentEdge v2 online — `{router.mode_label()}` mode")
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.warning(f"Telegram poll error: {e}")
                self._stop.wait(5)
        logger.info("Telegram listener stopped")

    def _poll_once(self):
        try:
            r = requests.get(
                f"{_API}{settings.TELEGRAM_TOKEN}/getUpdates",
                params={"offset": self._offset, "timeout": 25},
                timeout=30,
            )
            if r.status_code != 200:
                self._stop.wait(3)
                return
            updates = r.json().get("result", [])
        except requests.RequestException:
            self._stop.wait(3)
            return

        for u in updates:
            self._offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message")
            if not msg:
                continue
            chat_id = str(msg.get("chat", {}).get("id"))
            if chat_id != str(settings.TELEGRAM_CHAT_ID):
                continue  # ignore strangers
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            try:
                self._handle(text)
            except Exception as e:
                logger.exception(f"Telegram handler error: {e}")
                self.send(f"⚠ error: {e}")

    # ---------- command dispatch ----------
    def _handle(self, text: str):
        lower = text.lower()
        if lower in ("stop", "/stop"):
            self.send("Shutting down...")
            self._shutdown.set()
            return
        if lower.startswith("/status"):
            return self._cmd_status()
        if lower.startswith("/watchlist"):
            return self._cmd_watchlist()
        if lower.startswith("/closeall"):
            return self._cmd_closeall()
        if lower.startswith("/close "):
            return self._cmd_close(text.split(maxsplit=1)[1].strip().upper())
        if lower.startswith("/signal "):
            return self._cmd_signal(text.split(maxsplit=2)[1:])
        if lower.startswith("/reset"):
            return self._cmd_reset()
        if lower.startswith("/"):
            return self.send("Unknown command. Try /status /watchlist /closeall")
        # fallback: treat as asset query
        self._cmd_quick_analysis(text.strip().upper())

    def _cmd_status(self):
        stats = position_manager.stats()
        rstats = risk_manager.snapshot()
        positions = position_manager.open_positions()
        lines = [
            f"*Mode:* {stats['mode']}",
            f"*Win rate:* {stats['win_rate']}% ({stats['wins']}/{stats['total_trades']})",
            f"*Total PnL:* {stats['total_pnl']}",
            f"*Daily PnL:* {rstats['daily_pnl']} / -{rstats['daily_limit']}",
            f"*Open positions:* {len(positions)}",
        ]
        for p in positions:
            price = market_data.fetch_price(
                registry.get(p.asset).asset_class if registry.get(p.asset) else "crypto",
                registry.get(p.asset).exchange_symbol if registry.get(p.asset) else f"{p.asset}/USDT",
            )
            cur = price or p.entry_price
            lines.append(
                f"  `{p.asset}` {p.direction} qty={p.quantity} "
                f"entry={p.entry_price:.4f} now={cur:.4f} "
                f"pnl={p.pnl(cur):+.4f}"
            )
        self.send("\n".join(lines))

    def _cmd_watchlist(self):
        items = registry.all()
        line = ", ".join(f"`{a.symbol}`" for a in items)
        self.send(f"*Watchlist* ({len(items)}): {line}")

    def _cmd_closeall(self):
        results = position_manager.close_all(reason="telegram /closeall")
        self.send(f"Close-all: {len(results)} positions processed")

    def _cmd_close(self, asset: str):
        for p in position_manager.open_positions():
            if p.asset == asset:
                ok, msg = position_manager.close_position(p.id, "telegram /close")
                self.send(f"{asset}: {msg}")
                return
        self.send(f"No open position on {asset}")

    def _cmd_signal(self, args: list[str]):
        if len(args) < 1:
            return self.send("Usage: /signal ASSET LONG|SHORT")
        asset_name = args[0].upper()
        direction = (args[1] if len(args) > 1 else "LONG").upper()
        cfg = registry.get(asset_name)
        if not cfg:
            return self.send(f"{asset_name} not in watchlist")
        price = market_data.fetch_price(cfg.asset_class, cfg.exchange_symbol)
        if not price:
            return self.send(f"Could not fetch price for {asset_name}")
        sig = Signal(
            asset=asset_name, direction=direction, edge=80.0,
            price=price, strategy="manual", timeframe="manual",
            reason="manual /signal",
        )
        ok, msg = position_manager.try_open(sig, cfg, use_ai=False)
        self.send(f"Manual signal {asset_name} {direction}: {msg}")

    def _cmd_reset(self):
        if router.is_live():
            return self.send("❌ /reset refused in LIVE mode")
        ok = position_manager.reset_paper()
        self.send("✅ Paper stats reset" if ok else "❌ reset failed")

    def _cmd_quick_analysis(self, asset: str):
        cfg = registry.get(asset)
        if not cfg:
            return self.send(f"{asset} not in watchlist")
        candles = market_data.fetch_ohlcv(
            cfg.asset_class, cfg.exchange_symbol, "1h", 80
        )
        if not candles:
            return self.send(f"No data for {asset}")
        ind = compute_all(candles)
        if not ind:
            return self.send(f"Indicators not ready for {asset}")
        self.send(
            f"*{asset}* @ {ind['close'][-1]:.4f}\n"
            f"RSI: {ind['rsi'][-1]:.1f}\n"
            f"MACD hist: {ind['macd_hist'][-1]:.4f}\n"
            f"EMA9/21/50: {ind['ema9'][-1]:.4f} / "
            f"{ind['ema21'][-1]:.4f} / {ind['ema50'][-1]:.4f}\n"
            f"BB: [{ind['bb_lower'][-1]:.4f}, "
            f"{ind['bb_upper'][-1]:.4f}]\n"
            f"VWAP: {ind['vwap'][-1]:.4f}"
        )
