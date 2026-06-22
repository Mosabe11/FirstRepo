"""
execution/paper.py
------------------
Paper-trading executor. Simulates order fills at the analysis price
with a small configurable slippage. No real money touched.

Public API mirrors live_mexc.py so the router can swap them transparently.
"""

from __future__ import annotations
import logging
import random

logger = logging.getLogger(__name__)

# Slippage model: uniform between min and max (basis points)
SLIPPAGE_BPS_MIN = 1   # 0.01%
SLIPPAGE_BPS_MAX = 6   # 0.06%


def _slippage_factor(direction: str) -> float:
    bps = random.uniform(SLIPPAGE_BPS_MIN, SLIPPAGE_BPS_MAX)
    bps_decimal = bps / 10_000
    # buys fill worse (higher), sells fill worse (lower)
    if direction == "LONG":
        return 1 + bps_decimal
    return 1 - bps_decimal


def submit_order(asset_class: str, exchange_symbol: str,
                 direction: str, quantity: float,
                 expected_price: float) -> dict:
    """
    Returns dict with keys: filled, fill_price, fee, error.
    Always succeeds in paper mode (unless inputs are bad).
    """
    if quantity <= 0:
        return {"filled": False, "fill_price": 0, "fee": 0,
                "error": "qty must be > 0"}
    fill_price = expected_price * _slippage_factor(direction)
    # Taker fee per side. MEXC/Binance spot taker is ~0.05%-0.10%; we use a
    # conservative 0.075% (7.5 bps) to match the research backtester so paper
    # results are not rosier than the validated numbers. This fee is now
    # actually subtracted from PnL in execution/manager.py.
    fee = abs(fill_price * quantity) * 0.00075
    logger.info(
        f"[PAPER] {direction} {quantity} {exchange_symbol} "
        f"@ {fill_price:.6f} (expected {expected_price:.6f})"
    )
    return {
        "filled": True,
        "fill_price": float(fill_price),
        "fee": float(fee),
        "error": None,
        "mode": "paper",
    }


def close_order(asset_class: str, exchange_symbol: str,
                direction: str, quantity: float,
                expected_price: float) -> dict:
    """
    Closing a LONG = SELL; closing a SHORT = BUY.
    For paper, both routes are symmetric. Reuses submit_order to model fill.
    """
    closing_side = "SHORT" if direction == "LONG" else "LONG"
    return submit_order(asset_class, exchange_symbol,
                        closing_side, quantity, expected_price)
