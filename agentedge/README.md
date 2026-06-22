# AgentEdge v3.1 — Validated, Cost-Aware, Self-Learning

> **Read this first — honest status.** The original multi-strategy scalper had
> **no edge**: 866 paper trades, ~38% win rate, Profit Factor < 1.0. An
> independent, cost-aware backtest (`research/`) confirmed it on fresh real data.
> The root cause was structural, not a bug to tune away: 1m/5m signals whose
> per-trade move is too small to clear fees, made worse by an exit policy that
> strangled winners and let losers run (inverted reward:risk).
>
> **What changed in v3.1:**
> 1. **A real validation harness** (`research/`) — event-driven, no look-ahead,
>    models fees + slippage, splits in-sample/out-of-sample. *Run it before
>    trusting any strategy.* See [research/README.md](research/README.md).
> 2. **The losing scalpers are OFF by default.** The one configuration that
>    survived costs out-of-sample — a **daily, ADX-filtered trend strategy**
>    (`strategies/regime_trend.py`, OOS Profit Factor ~1.2) — is ON.
> 3. **Fixed the bleed:** R-multiple exits (`execution/position.py`) stop
>    strangling winners; **fees are now actually subtracted** from PnL; sizing
>    is **risk-based** (fixed % of equity per trade via the stop distance).
> 4. **Disciplined self-learning** (`core/learning.py`): the agent judges each
>    (asset, strategy) by its **own realized trades**, acts only after ≥20 of
>    them, uses Wilson confidence bounds, and **switches a losing combo off by
>    itself** — instead of fitting noise on 6 trades like the old weights.
>
> **The honest bottom line:** the daily trend edge is *promising but small-sample*
> (~20 OOS trades = anecdotal). It ships **paper-only**. Nobody — no tool, no
> model — can promise "a strategy that makes money with high confidence." What
> this rebuild gives you is the machinery to *prove* an edge before risking a
> cent, and to stop trading what doesn't work. Keep `LIVE_MODE=false` until the
> learning layer has a statistically meaningful live record.

---

# AgentEdge v2 — Automated Multi-Strategy Trading Agent

Paper-trading-first autonomous trading agent for crypto (MEXC) and metals (Yahoo Finance gold/silver). Combines the original v10 multi-bot architecture with 4 new low-timeframe scalping strategies from the strategy research doc. Built for clean separation, easy testing, and a one-flag flip from paper → live.

## Why v2

The v10 agent worked but mixed everything into one file: signals, execution, AI, dashboard, threading. v2 separates concerns so each piece can be tested, swapped, or extended independently.

- **Paper-first**: `LIVE_MODE=false` by default. Flipping it to `true` routes the same orders through real MEXC ccxt calls. Nothing else changes.
- **Strategy plugins**: Each strategy is a class with one `evaluate(candles) -> Signal` method. Add a new one by dropping a file in `strategies/`.
- **Two timeframes, one engine**: The original 1h swing logic AND the 4 new 1m/5m scalping strategies share the same execution, risk, and position-management layer.
- **Secrets in env**: No keys in code. Ever. `.env.example` shows what's needed.

## Repository Layout

```
agentedge_v2/
├── config/
│   ├── settings.py          # All env-var-driven config (single source of truth)
│   └── watchlist.py         # Base assets + asset class definitions
├── core/
│   ├── indicators.py        # EMA, RSI, MACD, BB, ATR, VWAP, ALMA, Keltner, Stoch
│   ├── market_data.py       # MEXC + Yahoo data fetchers (cached, rate-limited)
│   ├── ai_confirm.py        # DeepSeek BUY/SELL/HOLD confirmation
│   ├── risk.py              # Position sizing, drawdown limits, daily/weekly resets
│   └── signal.py            # Signal dataclass shared across strategies
├── strategies/
│   ├── base.py              # Abstract Strategy class
│   ├── swing_1h.py          # Original v10 technical scorer (EMA/RSI/MACD/BB)
│   ├── vwap_macd.py         # PDF strategy #1
│   ├── keltner_rsi.py       # PDF strategy #2
│   ├── alma_stoch.py        # PDF strategy #3
│   └── rsi_bb_revert.py     # PDF strategy #4
├── execution/
│   ├── paper.py             # Simulated fills, slippage modeling
│   ├── live_mexc.py         # Real ccxt MEXC execution (used when LIVE_MODE=true)
│   ├── position.py          # Position dataclass + TP/SL/trailing logic
│   └── router.py            # Picks paper vs live based on flag
├── bots/
│   ├── monitor.py           # Watches open positions, applies trailing stops
│   ├── trigger.py           # Fast scalping bot (2s loop) using PDF strategies
│   ├── auto_scanner.py      # Slow swing scanner (1h timeframe)
│   ├── discovery.py         # Finds new high-volume MEXC pairs
│   └── cleaner.py           # Prunes weak performers from watchlist
├── api/
│   ├── server.py            # HTTP server on :8080 + REST endpoints
│   └── telegram.py          # Bot listener + notifications
├── dashboard/
│   └── index.html           # Single-page dashboard (served by api/server.py)
├── storage/
│   └── state.py             # JSON persistence (positions, trades, learning stats)
├── main.py                  # Entrypoint — boots all threads
├── requirements.txt
├── .env.example
└── README.md
```

## Quick Start

```bash
# 1. Clone & enter
git clone <your-repo> agentedge_v2 && cd agentedge_v2

# 2. Python env
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Configure (PAPER MODE — safe defaults)
cp .env.example .env
# Edit .env: add DEEPSEEK_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
# Leave LIVE_MODE=false

# 4. Run
python main.py
```

Dashboard: `http://<your-server-ip>:8080`
Telegram: send `/status` to your bot

## Going Live (when you trust it)

```bash
# In .env:
LIVE_MODE=true
MEXC_API_KEY=...
MEXC_API_SECRET=...

# Restart:
python main.py
```

That's the entire flip. The bots, strategies, dashboard, Telegram — all unchanged. Only `execution/router.py` swaps `paper.py` for `live_mexc.py`.

## Strategies

| # | Strategy | Timeframe | File | Bot that uses it |
|---|----------|-----------|------|------------------|
| 0 | Swing scorer (v10 legacy) | 1h | `strategies/swing_1h.py` | `auto_scanner` |
| 1 | VWAP + MACD Momentum | 1m / 5m | `strategies/vwap_macd.py` | `trigger` |
| 2 | Keltner + RSI Reversal | 1m / 5m | `strategies/keltner_rsi.py` | `trigger` |
| 3 | ALMA + Stochastic | 1m | `strategies/alma_stoch.py` | `trigger` |
| 4 | RSI + Bollinger Mean Reversion | 1m / 5m | `strategies/rsi_bb_revert.py` | `trigger` |

The trigger bot evaluates all 4 scalping strategies in parallel on each watchlist asset. A trade fires only when at least one strategy gives a clear signal AND the DeepSeek AI confirms.

## Safety Defaults

- `MAX_POSITIONS=7` — never more than 7 open at once
- `DAILY_LIMIT=800` USD drawdown — hits → all new trades blocked until midnight
- `MAX_PRICE_DRIFT=0.02` — entry rejected if price moved >2% since analysis
- `LIVE_MODE=false` — must be explicitly enabled
- Paper trades default to $1000 simulated balance per asset class

## Telegram Commands

| Command | Action |
|---------|--------|
| `/status` | PnL, open positions, win rate |
| `/watchlist` | List tracked assets |
| `/closeall` | Close every open position |
| `/close BTC` | Close specific position |
| `/signal ETH LONG` | Force a manual trade |
| `/reset` | Wipe paper stats (paper only — refuses in live mode) |
| `STOP` | Graceful shutdown |
| anything else | Treats text as asset symbol, returns analysis |

## Persistence

State written to `./data/` (created on first run):
- `positions.json` — open trades
- `trades.json` — closed trade history
- `learning.json` — win rate per asset, per strategy, per market regime
- `logs/YYYY-MM-DD.log` — rotated daily, 7-day retention

On crash/restart, the agent reloads everything and resumes.

## What's New vs v10

| Concern | v10 | v2 |
|---------|-----|----|
| File count | 1 monolith | 25+ focused modules |
| Strategies | 1 scorer | 5 pluggable strategies |
| Timeframes | 1h only | 1m, 5m, 1h |
| Live mode | hardcoded paper | single env flag |
| Secrets | in-code | env-only |
| Testing | none | each strategy is unit-testable |
| AI calls | every trade | cached + only on top candidates |
| Per-strategy stats | none | win-rate tracked per strategy |

## Roadmap (not in this build)

- Reinforcement-learning agent that learns optimal strategy weights per regime (the PDF's last section)
- Backtesting harness with historical data replay
- Multi-account / multi-exchange support
- Options/futures via MEXC perpetuals
