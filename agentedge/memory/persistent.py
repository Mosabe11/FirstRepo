"""
memory/persistent.py — Learn from past trades.

Stores every trade decision (open + close + outcome) in a SQLite DB.
Before each new trade, queries similar past trades and provides context
to the Council.

Schema:
  trades(id, asset, direction, strategy, entry_rsi, entry_macd_hist,
         regime, ai_decision, ai_confidence, opened_at, closed_at,
         pnl, pnl_pct, close_reason, win)

  insights(asset, key, value, updated_at)  -- aggregated lessons
"""
import time
import logging
import sqlite3
import threading
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

DB_PATH = settings.DATA_DIR / "memory.db"
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    """Create tables if not exist."""
    DB_PATH.parent.mkdir(exist_ok=True)
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT,
                asset TEXT,
                direction TEXT,
                strategy TEXT,
                entry_price REAL,
                exit_price REAL,
                quantity REAL,
                pnl REAL,
                pnl_pct REAL,
                win INTEGER,
                opened_at REAL,
                closed_at REAL,
                close_reason TEXT,
                entry_rsi REAL,
                entry_macd_hist REAL,
                entry_atr REAL,
                regime TEXT,
                council_decision TEXT,
                council_confidence REAL,
                signal_edge REAL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_asset ON trades(asset)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at DESC)")
    logger.info(f"Memory DB initialized at {DB_PATH}")


def record_trade_open(trade_id, asset, direction, strategy, entry_price, quantity,
                      entry_rsi=None, entry_macd_hist=None, entry_atr=None,
                      regime=None, council_decision=None, council_confidence=None,
                      signal_edge=None):
    """Called when a position is opened. Trade is finalized in record_trade_close."""
    with _lock, _conn() as c:
        c.execute("""
            INSERT INTO trades (
                trade_id, asset, direction, strategy, entry_price, quantity,
                opened_at, entry_rsi, entry_macd_hist, entry_atr, regime,
                council_decision, council_confidence, signal_edge
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade_id, asset, direction, strategy, entry_price, quantity,
            time.time(), entry_rsi, entry_macd_hist, entry_atr, regime,
            council_decision, council_confidence, signal_edge,
        ))


def record_trade_close(trade_id, exit_price, pnl, pnl_pct, close_reason):
    with _lock, _conn() as c:
        c.execute("""
            UPDATE trades
            SET exit_price=?, pnl=?, pnl_pct=?, win=?, closed_at=?, close_reason=?
            WHERE trade_id=?
        """, (
            exit_price, pnl, pnl_pct, 1 if (pnl or 0) > 0 else 0,
            time.time(), close_reason, trade_id,
        ))


def query_similar(asset, direction, strategy, rsi=None, regime=None, limit=5):
    """
    Find recent trades that match these conditions.
    Returns most recent matches first.
    """
    with _conn() as c:
        c.row_factory = sqlite3.Row
        q = "SELECT * FROM trades WHERE asset=? AND direction=? AND closed_at IS NOT NULL"
        params = [asset, direction]
        if strategy:
            q += " AND strategy=?"
            params.append(strategy)
        if regime:
            q += " AND regime=?"
            params.append(regime)
        q += " ORDER BY closed_at DESC LIMIT ?"
        params.append(limit)
        rows = c.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def stats_by_strategy(asset=None):
    """Win rate per strategy, optionally filtered by asset."""
    with _conn() as c:
        q = "SELECT strategy, COUNT(*) as n, SUM(win) as wins, SUM(pnl) as total_pnl FROM trades WHERE closed_at IS NOT NULL"
        params = []
        if asset:
            q += " AND asset=?"
            params.append(asset)
        q += " GROUP BY strategy"
        rows = c.execute(q, params).fetchall()
        return [{"strategy": r[0], "n": r[1], "wins": r[2],
                 "win_rate": (r[2]/r[1]) if r[1] else 0,
                 "total_pnl": r[3] or 0} for r in rows]


def stats_by_asset():
    """Win rate per asset."""
    with _conn() as c:
        rows = c.execute("""
            SELECT asset, COUNT(*) as n, SUM(win) as wins, SUM(pnl) as total_pnl
            FROM trades WHERE closed_at IS NOT NULL
            GROUP BY asset
        """).fetchall()
        return [{"asset": r[0], "n": r[1], "wins": r[2],
                 "win_rate": (r[2]/r[1]) if r[1] else 0,
                 "total_pnl": r[3] or 0} for r in rows]



def reconcile_ghosts(live_trade_ids):
    """عند الإقلاع: علّم أي صفقة مفتوحة بالسجل وغير موجودة في الذاكرة الحيّة كـ ABANDONED.
    live_trade_ids: مجموعة trade_id للمراكز المفتوحة فعلياً في state.json."""
    live = set(str(t) for t in (live_trade_ids or []))
    with _lock, _conn() as c:
        open_rows = c.execute(
            "SELECT trade_id, opened_at FROM trades WHERE closed_at IS NULL"
        ).fetchall()
        ghosts = [(tid, op) for (tid, op) in open_rows if str(tid) not in live]
        for tid, op in ghosts:
            c.execute(
                "UPDATE trades SET closed_at=?, close_reason='ABANDONED', "
                "win=0, pnl=0, pnl_pct=0 WHERE trade_id=? AND closed_at IS NULL",
                (op or time.time(), tid),
            )
    if ghosts:
        logger.warning(f"reconcile_ghosts: علّمت {len(ghosts)} صفقة معلّقة كـ ABANDONED")
    return len(ghosts)


def build_context_for_council(asset, direction, strategy, regime=None):
    """
    Build a short text snippet to give the Judge agent context from past trades.
    Returns empty string if no relevant history.
    """
    similar = query_similar(asset, direction, strategy, regime=regime, limit=5)
    if not similar:
        return ""

    wins = sum(1 for t in similar if t.get("win"))
    losses = len(similar) - wins
    avg_pnl = sum(t.get("pnl", 0) for t in similar) / len(similar)

    lines = [
        f"📚 Memory: Last {len(similar)} similar trades on {asset} {direction}",
        f"   Strategy: {strategy} | Wins: {wins}, Losses: {losses}, Avg PnL: {avg_pnl:+.4f}",
    ]
    if losses > wins and len(similar) >= 3:
        lines.append(f"   ⚠️ Historical pattern: this setup has LOST more than won.")
    elif wins > losses and len(similar) >= 3:
        lines.append(f"   ✅ Historical pattern: this setup has WON more than lost.")
    return "\n".join(lines)


def get_winrate_modifier(asset, strategy):
    """
    Returns a multiplier 0.5–1.5 based on this asset+strategy history.
    Used by RiskManager for position sizing.
    """
    rows = stats_by_strategy(asset)
    for r in rows:
        if r["strategy"] == strategy and r["n"] >= 5:
            wr = r["win_rate"]
            # Map win rate to multiplier: 0.3 wr → 0.7x, 0.7 wr → 1.3x
            return max(0.5, min(1.5, 0.5 + wr * 1.5))
    return 1.0  # Unknown — neutral


# Init on import
init()
