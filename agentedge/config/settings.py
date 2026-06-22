"""
config/settings.py
------------------
Single source of truth for runtime configuration.
Everything is loaded from environment variables (via .env).
No secrets in code. No magic numbers scattered across modules.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name, str(default)).strip().lower()
    return val in ("true", "1", "yes", "y", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# ============================================================
# EXECUTION MODE
# ============================================================
LIVE_MODE: bool = _bool("LIVE_MODE", False)

# ============================================================
# API CREDENTIALS
# ============================================================
MEXC_API_KEY = os.getenv("MEXC_API_KEY", "")
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET", "")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# NETWORK
# ============================================================
HTTP_PORT: int = _int("HTTP_PORT", 8080)

# ============================================================
# RISK MANAGEMENT
# ============================================================
MAX_POSITIONS: int = _int("MAX_POSITIONS", 7)
DAILY_LIMIT: float = _float("DAILY_LIMIT", 800)
WEEKLY_LIMIT: float = _float("WEEKLY_LIMIT", 3000)
MAX_PRICE_DRIFT: float = _float("MAX_PRICE_DRIFT", 0.02)  # 2%
PAPER_STARTING_BALANCE: float = _float("PAPER_STARTING_BALANCE", 10000)

# Default TP/SL percentages by asset class
DEFAULT_TP_PCT = 0.015  # 1.5%
DEFAULT_SL_PCT = 0.008  # 0.8%
BREAKEVEN_TRIGGER_PCT = _float("BREAKEVEN_TRIGGER_PCT", 0.008)  # +0.8% before BE
TRAILING_DISTANCE_PCT = _float("TRAILING_DISTANCE_PCT", 0.006)  # 0.6% trailing distance

# Per-hour trade limit (anti-overtrade)
MAX_TRADES_PER_HOUR = 3
TRIGGER_COOLDOWN_AFTER_TRADE = 60  # seconds
TRIGGER_COOLDOWN_IF_OPEN = 180

# ============================================================
# STRATEGY TOGGLES
# ============================================================
ENABLE_SWING_1H: bool = _bool("ENABLE_SWING_1H", True)
ENABLE_VWAP_MACD: bool = _bool("ENABLE_VWAP_MACD", True)
ENABLE_KELTNER_RSI: bool = _bool("ENABLE_KELTNER_RSI", True)
ENABLE_ALMA_STOCH: bool = _bool("ENABLE_ALMA_STOCH", True)
ENABLE_RSI_BB_REVERT: bool = _bool("ENABLE_RSI_BB_REVERT", True)

# ============================================================
# BOT TOGGLES
# ============================================================
ENABLE_MONITOR: bool = _bool("ENABLE_MONITOR", True)
ENABLE_TRIGGER: bool = _bool("ENABLE_TRIGGER", True)
ENABLE_AUTO_SCANNER: bool = _bool("ENABLE_AUTO_SCANNER", True)
ENABLE_DISCOVERY: bool = _bool("ENABLE_DISCOVERY", True)
ENABLE_CLEANER: bool = _bool("ENABLE_CLEANER", True)

# ============================================================
# BOT TIMING (seconds)
# ============================================================
MONITOR_INTERVAL: int = 5
TRIGGER_INTERVAL: int = _int("TRIGGER_INTERVAL", 2)
AUTO_SCAN_INTERVAL: int = _int("AUTO_SCAN_INTERVAL", 120)
DISCOVERY_INTERVAL: int = _int("DISCOVERY_INTERVAL", 60)
CLEANER_INTERVAL: int = _int("CLEANER_INTERVAL", 300)

# ============================================================
# SIGNAL THRESHOLDS
# ============================================================
TRIGGER_EDGE: float = _float("TRIGGER_EDGE", 75)
AUTO_ENTRY: float = _float("AUTO_ENTRY", 60)
AI_MIN_CONFIDENCE: float = _float("AI_MIN_CONFIDENCE", 60)

# ============================================================
# WATCHLIST LIMITS
# ============================================================
WATCHLIST_MAX_SIZE: int = _int("WATCHLIST_MAX_SIZE", 30)
DISCOVERY_BATCH_SIZE: int = 8
DISCOVERY_MIN_VOLUME_USDT: float = 1_000_000

# ============================================================
# DATA DIRECTORIES
# ============================================================
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Log retention in days
LOG_RETENTION_DAYS = 7

# ============================================================
# AI CACHE
# ============================================================
AI_CACHE_TTL_SECONDS = 300  # 5 minutes


def validate() -> list[str]:
    """Return list of missing/invalid required config. Empty = OK."""
    errors = []
    if not DEEPSEEK_KEY:
        errors.append("DEEPSEEK_KEY not set (AI confirmation will be skipped)")
    if not TELEGRAM_TOKEN:
        errors.append("TELEGRAM_TOKEN not set (Telegram disabled)")
    if not TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID not set (Telegram disabled)")
    if LIVE_MODE:
        if not MEXC_API_KEY or not MEXC_API_SECRET:
            errors.append(
                "LIVE_MODE=true but MEXC_API_KEY/SECRET missing — REFUSING TO START LIVE"
            )
    return errors


def summary() -> str:
    """Human-readable startup banner."""
    mode = "🔴 LIVE" if LIVE_MODE else "🟢 PAPER"
    return (
        f"\n{'='*60}\n"
        f"  AgentEdge v2  |  Mode: {mode}\n"
        f"  Dashboard: http://0.0.0.0:{HTTP_PORT}\n"
        f"  Max positions: {MAX_POSITIONS}  |  Daily limit: ${DAILY_LIMIT}\n"
        f"  Strategies: "
        + ", ".join(
            n
            for n, on in [
                ("swing", ENABLE_SWING_1H),
                ("vwap-macd", ENABLE_VWAP_MACD),
                ("keltner-rsi", ENABLE_KELTNER_RSI),
                ("alma-stoch", ENABLE_ALMA_STOCH),
                ("rsi-bb", ENABLE_RSI_BB_REVERT),
            ]
            if on
        )
        + f"\n{'='*60}\n"
    )

# ============================================================
# v3 — Advanced Intelligence Features
# ============================================================
ENABLE_COUNCIL = _bool("ENABLE_COUNCIL", False)
ENABLE_MEMORY = _bool("ENABLE_MEMORY", True)
ENABLE_NEWS_FILTER = _bool("ENABLE_NEWS_FILTER", True)
ENABLE_ADAPTIVE_WEIGHTS = _bool("ENABLE_ADAPTIVE_WEIGHTS", True)
ENABLE_BACKTEST_BOT = _bool("ENABLE_BACKTEST_BOT", True)
BACKTEST_INTERVAL = _int("BACKTEST_INTERVAL", 21600)  # 6h
ENABLE_EMA_CROSS = _bool("ENABLE_EMA_CROSS", False)
