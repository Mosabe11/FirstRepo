"""
api/server.py
-------------
HTTP server on settings.HTTP_PORT serving:
  /              dashboard HTML
  /api/status    JSON: stats, risk, mode
  /api/positions JSON: open positions
  /api/trades    JSON: recent closed trades
  /api/watchlist JSON: current watchlist
  /api/analyze?asset=BTC   JSON: live indicators for asset
  /api/close?id=...        POST equivalent — close one
  /api/closeall            POST equivalent — close all
  /api/open?asset=BTC&dir=LONG  manual open

Auto-restarts itself if it crashes. Single threaded HTTP server is fine
since the work is tiny and the JSON is held in memory.
"""

from __future__ import annotations
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path

from config import settings
from core.signal import Signal
from core import market_data
from core.indicators import compute_all
from core.risk import risk_manager
from execution.manager import position_manager
from execution import router
from storage.watchlist_runtime import registry

logger = logging.getLogger(__name__)

DASHBOARD_HTML_PATH = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence access log
        return

    def _send_json(self, payload, status: int = 200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            try:
                html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
            except Exception:
                html = "<h1>AgentEdge v2</h1><p>dashboard HTML missing</p>"
            return self._send_html(html)

        if path == "/api/status":
            return self._send_json({
                "stats": position_manager.stats(),
                "risk": risk_manager.snapshot(),
                "mode": router.mode_label(),
            })

        if path == "/api/positions":
            return self._send_json([
                {**p.to_dict(),
                 "current_price": market_data.fetch_price(
                     registry.get(p.asset).asset_class if registry.get(p.asset) else "crypto",
                     registry.get(p.asset).exchange_symbol if registry.get(p.asset) else f"{p.asset}/USDT",
                 )}
                for p in position_manager.open_positions()
            ])

        if path == "/api/trades":
            return self._send_json(position_manager.recent_trades(30))

        if path == "/api/watchlist":
            return self._send_json([
                {"symbol": a.symbol, "asset_class": a.asset_class,
                 "exchange_symbol": a.exchange_symbol, "is_base": a.is_base}
                for a in registry.all()
            ])

        if path == "/api/analyze":
            asset = (params.get("asset", [""])[0]).upper()
            cfg = registry.get(asset)
            if not cfg:
                return self._send_json({"error": f"{asset} not in watchlist"}, 404)
            candles = market_data.fetch_ohlcv(
                cfg.asset_class, cfg.exchange_symbol, "1h", 80
            )
            if not candles:
                return self._send_json({"error": "no data"}, 502)
            ind = compute_all(candles)
            if not ind:
                return self._send_json({"error": "indicators not ready"}, 503)
            keys = ["close", "rsi", "macd_hist", "ema9", "ema21",
                    "ema50", "bb_upper", "bb_lower", "vwap", "atr"]
            return self._send_json({
                "asset": asset,
                "price": float(ind["close"][-1]),
                **{k: float(ind[k][-1]) for k in keys if k in ind},
            })

        if path == "/api/position":
            pos_id = params.get("id", [""])[0]
            cfg = None
            position = None
            for p in position_manager.open_positions():
                if p.id == pos_id:
                    position = p
                    cfg = registry.get(p.asset)
                    break
            if not position:
                return self._send_json({"error": "not found"}, 404)

            cur_price = market_data.fetch_price(cfg.asset_class, cfg.exchange_symbol) if cfg else position.entry_price

            # candles for chart
            chart_candles = []
            if cfg:
                raw = market_data.fetch_ohlcv(cfg.asset_class, cfg.exchange_symbol, "5m", 60) or []
                chart_candles = [
                    {"t": c[0], "o": float(c[1]), "h": float(c[2]),
                     "l": float(c[3]), "c": float(c[4])}
                    for c in raw
                ]

            # memory record for council info
            memory_record = None
            try:
                import sqlite3
                conn = sqlite3.connect("data/memory.db")
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM trades WHERE trade_id=?", (pos_id,)).fetchone()
                if row:
                    memory_record = dict(row)
                conn.close()
            except Exception:
                pass

            return self._send_json({
                "position": position.to_dict(),
                "current_price": cur_price,
                "candles": chart_candles,
                "memory": memory_record,
            })

        if path == "/api/close":
            pos_id = params.get("id", [""])[0]
            ok, msg = position_manager.close_position(pos_id, "dashboard close")
            return self._send_json({"ok": ok, "message": msg})

        if path == "/api/closeall":
            results = position_manager.close_all(reason="dashboard closeall")
            return self._send_json({"closed": len(results), "results": results})

        if path == "/api/open":
            asset = (params.get("asset", [""])[0]).upper()
            direction = (params.get("dir", ["LONG"])[0]).upper()
            cfg = registry.get(asset)
            if not cfg:
                return self._send_json({"ok": False, "message": "unknown asset"}, 404)
            price = market_data.fetch_price(cfg.asset_class, cfg.exchange_symbol)
            if not price:
                return self._send_json({"ok": False, "message": "no price"}, 502)
            sig = Signal(
                asset=asset, direction=direction, edge=80,
                price=price, strategy="manual_dashboard",
                timeframe="manual", reason="manual open",
            )
            ok, msg = position_manager.try_open(sig, cfg, use_ai=False)
            return self._send_json({"ok": ok, "message": msg})

        return self._send_json({"error": "not found"}, 404)


class HttpServerBot:
    name = "http_server"

    def __init__(self):
        self._stop = threading.Event()
        self._server: ThreadingHTTPServer | None = None

    def stop(self):
        self._stop.set()
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass

    def run(self):
        logger.info(f"HTTP server starting on :{settings.HTTP_PORT}")
        while not self._stop.is_set():
            try:
                self._server = ThreadingHTTPServer(
                    ("0.0.0.0", settings.HTTP_PORT), Handler
                )
                self._server.serve_forever(poll_interval=1)
            except Exception as e:
                logger.exception(f"HTTP server crashed, restarting in 5s: {e}")
                self._stop.wait(5)
        logger.info("HTTP server stopped")
