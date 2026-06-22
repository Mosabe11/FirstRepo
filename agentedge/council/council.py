"""
council/council.py — Multi-Agent Council that deliberates before each trade.

4 specialized agents:
  1. TechnicalAnalyst   — Reads indicators (RSI, MACD, etc.)
  2. RiskManager        — Evaluates trade risk
  3. RegimeDetector     — Identifies market regime (bull/bear/sideways)
  4. FinalJudge         — Synthesizes votes into BUY/SELL/HOLD

Each agent is a DeepSeek call with a focused system prompt.
The Judge sees all 3 reports and makes the final call.
"""
import json
import time
import logging
import threading
import requests

from config import settings
from core.notify import tg_send

logger = logging.getLogger(__name__)
_API_URL = "https://api.deepseek.com/v1/chat/completions"

# Cache for council decisions
_cache = {}
_cache_lock = threading.Lock()
_call_count = 0
_call_reset = 0
_MAX_CALLS_PER_HOUR = 10
_rate_lock = threading.Lock()


def _check_rate_limit():
    global _call_count, _call_reset
    with _rate_lock:
        now = time.time()
        if now - _call_reset > 3600:
            _call_count = 0
            _call_reset = now
        if _call_count >= _MAX_CALLS_PER_HOUR:
            return False
        _call_count += 1
        return True


def _cache_get(key):
    with _cache_lock:
        if key in _cache:
            ts, val = _cache[key]
            if time.time() - ts < 900:
                return val
            del _cache[key]
    return None


def _cache_put(key, val):
    with _cache_lock:
        _cache[key] = (time.time(), val)


def _call_deepseek(system_prompt, user_prompt, max_tokens=300):
    """Generic DeepSeek call. Returns parsed JSON or None."""
    if not settings.DEEPSEEK_KEY:
        return None
    try:
        r = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens": max_tokens,
            },
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning(f"Council DeepSeek HTTP {r.status_code}")
            return None
        content = r.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()
        # Try direct parse first, then fallback extraction
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import re
            # Extract key fields manually
            vote = re.search(r'"(?:vote|decision|regime)"\s*:\s*"(\w+)"', content)
            conf = re.search(r'"(?:confidence|strength)"\s*:\s*(\d+)', content)
            reason = re.search(r'"(?:reasoning|summary)"\s*:\s*"([^"]*)"', content)
            if vote:
                result = {}
                key = "vote" if "vote" in content[:50] else "decision" if "decision" in content[:50] else "regime"
                result[key] = vote.group(1).upper()
                if conf:
                    result["confidence"] = int(conf.group(1))
                    result["strength"] = int(conf.group(1))
                if reason:
                    result["reasoning"] = reason.group(1)
                    result["summary"] = reason.group(1)
                return result
            return None
    except Exception as e:
        logger.warning(f"Council call failed: {e}")
        return None


def _agent_technical(asset, direction, price, indicators):
    """Technical analyst — reads price action signals."""
    snap = {}
    for k in ["rsi", "macd_hist", "ema9", "ema21", "ema50",
              "bb_upper", "bb_lower", "vwap", "atr"]:
        v = indicators.get(k)
        if v is None:
            continue
        try:
            val = v[-1] if hasattr(v, "__len__") else v
            snap[k] = round(float(val), 6)
        except Exception:
            pass

    return _call_deepseek(
        system_prompt=(
            "You are a technical analyst. Evaluate ONLY price/indicator signals. "
            "Ignore fundamentals or news. Respond ONLY in JSON: "
            '{"vote":"BUY|SELL|HOLD","confidence":0-100,"reasoning":"short technical explanation"}'
        ),
        user_prompt=(
            f"Asset: {asset}\nProposed direction: {direction}\nPrice: {price}\n"
            f"Indicators: {json.dumps(snap)}\n\n"
            f"Based purely on technical analysis, vote on this trade."
        ),
        max_tokens=200,
    )


def _agent_risk(asset, direction, price, indicators, risk_snapshot):
    """Risk manager — focuses on downside risk and portfolio health."""
    atr_val = None
    try:
        atr_arr = indicators.get("atr")
        if atr_arr is not None:
            atr_val = float(atr_arr[-1])
    except Exception:
        pass

    return _call_deepseek(
        system_prompt=(
            "You are a risk manager. Focus on downside scenarios, position sizing, "
            "and overall portfolio risk. Be conservative. Respond ONLY in JSON: "
            '{"vote":"BUY|SELL|HOLD","confidence":0-100,"reasoning":"risk perspective"}'
        ),
        user_prompt=(
            f"Asset: {asset}\nProposed direction: {direction}\nPrice: {price}\n"
            f"ATR (volatility): {atr_val}\n"
            f"Portfolio state:\n"
            f"  - Daily PnL: {risk_snapshot['daily_pnl']} (limit -{risk_snapshot['daily_limit']})\n"
            f"  - Weekly PnL: {risk_snapshot['weekly_pnl']} (limit -{risk_snapshot['weekly_limit']})\n"
            f"  - Trades this hour: {risk_snapshot['trades_last_hour']}\n\n"
            f"Vote on this trade from a pure risk-management perspective."
        ),
        max_tokens=200,
    )


def _agent_regime(asset, candles_summary):
    """Market regime detector — bull/bear/sideways."""
    return _call_deepseek(
        system_prompt=(
            "You are a market regime detector. Classify the current market as "
            "BULL/BEAR/SIDEWAYS based on recent price behavior. Respond ONLY in JSON: "
            '{"regime":"BULL|BEAR|SIDEWAYS","strength":0-100,"reasoning":"short"}'
        ),
        user_prompt=(
            f"Asset: {asset}\nRecent price summary: {candles_summary}\n\n"
            f"What is the current market regime?"
        ),
        max_tokens=150,
    )


def _agent_judge(asset, direction, technical, risk, regime, memory_context=""):
    """Final judge — synthesizes the 3 reports."""
    tech_str = json.dumps(technical) if technical else "unavailable"
    risk_str = json.dumps(risk) if risk else "unavailable"
    regime_str = json.dumps(regime) if regime else "unavailable"

    return _call_deepseek(
        system_prompt=(
            "You are the chief trading judge. Three specialists have voted on a trade: "
            "Technical Analyst, Risk Manager, and Market Regime Detector. "
            "Synthesize their views into a final decision. "
            "If risk says HOLD or technical disagrees with the proposal, lean conservative. "
            "Respond ONLY in JSON: "
            '{"decision":"BUY|SELL|HOLD","confidence":0-100,"reasoning":"final synthesis"}'
        ),
        user_prompt=(
            f"Asset: {asset}\nProposed direction: {direction}\n\n"
            f"Technical Analyst vote: {tech_str}\n"
            f"Risk Manager vote: {risk_str}\n"
            f"Regime Detector: {regime_str}\n"
            f"{memory_context}\n\n"
            f"Rules: If 2 out of 3 agents agree with the proposed direction, approve it. "
            f"Only HOLD if there is strong contradiction or high risk. "
            f"Be decisive — too many HOLDs means missed opportunities. "
            f"Make the final call."
        ),
        max_tokens=250,
    )


def _candles_summary(candles):
    """Build a short text summary of recent price action for the regime agent."""
    if not candles or len(candles) < 5:
        return "insufficient data"
    closes = [c[4] for c in candles[-20:]]
    high = max(c[2] for c in candles[-20:])
    low = min(c[3] for c in candles[-20:])
    change_pct = (closes[-1] - closes[0]) / closes[0] * 100
    return (
        f"Last 20 bars: open={closes[0]:.4f}, close={closes[-1]:.4f}, "
        f"high={high:.4f}, low={low:.4f}, change={change_pct:+.2f}%"
    )


def deliberate(asset, direction, price, indicators, candles, risk_snapshot,
               memory_context=""):
    """
    Run the council. Returns (decision, confidence, votes_dict).
      decision: "BUY" / "SELL" / "HOLD" / None (on total failure)
      confidence: 0-100
      votes_dict: full record of every agent's response
    """
    if not settings.DEEPSEEK_KEY:
        return None, 0.0, {}
    if not _check_rate_limit():
        logger.debug("Council rate limit reached — skipping")
        return None, 0.0, {}

    # Cache
    px_bucket = round(price * 100) / 100 if price < 100 else round(price)
    key = f"council:{asset}:{direction}:{px_bucket}"
    cached = _cache_get(key)
    if cached:
        return cached

    votes = {}

    # === Agent 1: Technical ===
    technical = _agent_technical(asset, direction, price, indicators)
    votes["technical"] = technical

    # === Agent 2: Risk ===
    risk = _agent_risk(asset, direction, price, indicators, risk_snapshot)
    votes["risk"] = risk

    # === Agent 3: Regime ===
    regime = _agent_regime(asset, _candles_summary(candles))
    votes["regime"] = regime

    # === Agent 4: Judge ===
    judgment = _agent_judge(asset, direction, technical, risk, regime, memory_context)
    votes["judge"] = judgment

    if not judgment:
        return None, 0.0, votes

    decision = str(judgment.get("decision", "HOLD")).upper()
    confidence = float(judgment.get("confidence", 0))
    if decision not in ("BUY", "SELL", "HOLD"):
        decision = "HOLD"

    result = (decision, confidence, votes)
    _cache_put(key, result)

    # Telegram notification — show the full council deliberation
    tech_vote = (technical or {}).get("vote", "?")
    risk_vote = (risk or {}).get("vote", "?")
    regime_str = (regime or {}).get("regime", "?")
    emoji = "🟢" if decision == "BUY" else "🔴" if decision == "SELL" else "⚪"
    tg_send(
        f"{emoji} *Council Verdict* `{asset}`\n"
        f"Proposed: {direction}\n"
        f"━━━━━━━━━━━━━\n"
        f"🔬 Technical: *{tech_vote}* ({(technical or {}).get('confidence', 0):.0f}%)\n"
        f"⚖️ Risk: *{risk_vote}* ({(risk or {}).get('confidence', 0):.0f}%)\n"
        f"📊 Regime: *{regime_str}*\n"
        f"━━━━━━━━━━━━━\n"
        f"⚡ *Judge: {decision}* ({confidence:.0f}%)\n"
        f"_{(judgment or {}).get('reasoning', '')[:150]}_",
        category="ai",
    )

    return result
