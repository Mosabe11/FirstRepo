"""
storage/state.py
----------------
Atomic JSON persistence for positions, trades, and learning stats.

Writes go to a temp file then rename — so a crash mid-write
never produces a corrupt state file.
"""

from __future__ import annotations
import os
import json
import logging
import threading
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

STATE_PATH = settings.DATA_DIR / "state.json"
WATCHLIST_PATH = settings.DATA_DIR / "watchlist.json"

_lock = threading.Lock()


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"State load failed, starting fresh: {e}")
        return {}


def save_state(state: dict) -> None:
    with _lock:
        tmp = STATE_PATH.with_suffix(".json.tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, default=str)
            os.replace(tmp, STATE_PATH)
        except Exception as e:
            logger.error(f"State save failed: {e}")


def load_watchlist() -> list[dict]:
    if not WATCHLIST_PATH.exists():
        return []
    try:
        with open(WATCHLIST_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_watchlist(assets: list[dict]) -> None:
    with _lock:
        tmp = WATCHLIST_PATH.with_suffix(".json.tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(assets, f, indent=2, default=str)
            os.replace(tmp, WATCHLIST_PATH)
        except Exception as e:
            logger.error(f"Watchlist save failed: {e}")
