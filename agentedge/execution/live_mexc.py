"""
execution/live_mexc.py
----------------------
LIVE executor — places real orders on MEXC spot via ccxt.
Activated only when settings.LIVE_MODE is True.

⚠ THIS USES REAL FUNDS. Do not enable until paper-mode performance
is satisfactory and risk parameters are well-tested.

Same function signatures as paper.py so router.py can swap them.
"""

from __future__ import annotations
import logging
import threading
from typing import Optional

import ccxt

from config import settings

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client: Optional[ccxt.mexc] = None


def _get_client() -> ccxt.mexc:
    global _client
    with _client_lock:
        if _client is None:
            if not settings.MEXC_API_KEY or not settings.MEXC_API_SECRET:
                raise RuntimeError(
                    "LIVE mode requires MEXC_API_KEY and MEXC_API_SECRET"
                )
            _client = ccxt.mexc({
                "apiKey": settings.MEXC_API_KEY,
                "secret": settings.MEXC_API_SECRET,
                "enableRateLimit": True,
                "timeout": 15000,
                "options": {"defaultType": "spot"},
            })
        return _client


def submit_order(asset_class: str, exchange_symbol: str,
                 direction: str, quantity: float,
                 expected_price: float) -> dict:
    """
    Place a market order. MEXC spot only supports LONG (buy) and selling
    what you hold. SHORT here is interpreted as 'sell asset' (futures
    support is out of scope for v2 initial).
    """
    if asset_class != "crypto":
        return {"filled": False, "fill_price": 0, "fee": 0,
                "error": f"live trading not supported for {asset_class}"}

    if direction == "SHORT":
        return {"filled": False, "fill_price": 0, "fee": 0,
                "error": "live shorts require futures account "
                         "(spot live trading is long-only in v2)"}

    try:
        client = _get_client()
        side = "buy" if direction == "LONG" else "sell"
        order = client.create_order(
            symbol=exchange_symbol,
            type="market",
            side=side,
            amount=quantity,
        )
        avg = float(order.get("average") or order.get("price") or expected_price)
        fee = 0.0
        fees = order.get("fees") or []
        if fees:
            try:
                fee = float(fees[0].get("cost", 0))
            except Exception:
                pass
        logger.warning(
            f"[LIVE] {direction} {quantity} {exchange_symbol} @ {avg} "
            f"(order id {order.get('id')})"
        )
        return {
            "filled": True,
            "fill_price": avg,
            "fee": fee,
            "error": None,
            "mode": "live",
            "order_id": order.get("id"),
        }
    except Exception as e:
        logger.error(f"[LIVE] order failed: {e}")
        return {"filled": False, "fill_price": 0, "fee": 0, "error": str(e)}


def close_order(asset_class: str, exchange_symbol: str,
                direction: str, quantity: float,
                expected_price: float) -> dict:
    """Close = opposite of open."""
    if direction == "LONG":
        # we're long → sell to close
        return submit_order(asset_class, exchange_symbol,
                            "SHORT", quantity, expected_price)
    return submit_order(asset_class, exchange_symbol,
                        "LONG", quantity, expected_price)
