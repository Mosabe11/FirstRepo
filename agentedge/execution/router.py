"""
execution/router.py
-------------------
Single switching point: paper vs live.

If settings.LIVE_MODE is true (and required credentials are present),
all order submissions go to live_mexc. Otherwise they go to paper.

THIS IS THE ONE FILE THAT MATTERS WHEN FLIPPING THE FLAG.
"""

from __future__ import annotations
import logging

from config import settings
from execution import paper, live_mexc

logger = logging.getLogger(__name__)


def is_live() -> bool:
    """True only if config says live AND keys are present."""
    return bool(
        settings.LIVE_MODE
        and settings.MEXC_API_KEY
        and settings.MEXC_API_SECRET
    )


def submit_order(asset_class: str, exchange_symbol: str,
                 direction: str, quantity: float,
                 expected_price: float) -> dict:
    if is_live():
        return live_mexc.submit_order(asset_class, exchange_symbol,
                                      direction, quantity, expected_price)
    return paper.submit_order(asset_class, exchange_symbol,
                              direction, quantity, expected_price)


def close_order(asset_class: str, exchange_symbol: str,
                direction: str, quantity: float,
                expected_price: float) -> dict:
    if is_live():
        return live_mexc.close_order(asset_class, exchange_symbol,
                                     direction, quantity, expected_price)
    return paper.close_order(asset_class, exchange_symbol,
                             direction, quantity, expected_price)


def mode_label() -> str:
    return "LIVE" if is_live() else "PAPER"
