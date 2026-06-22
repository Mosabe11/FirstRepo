# research/ — the honest validation harness

Nothing here trades. This is the lab that answers one question before any
strategy is allowed near money:

> **Does this have a real, out-of-sample, cost-aware edge — or am I fooling myself?**

It follows the discipline of the *quant-trading-backtesting* skill: validate
data → backtest without look-ahead → compute the metrics that matter → check
out-of-sample → model costs honestly. Win rate alone is meaningless.

## How to run

From the `agentedge/` directory (needs `numpy`, `pandas`, `ccxt`):

```bash
# 1. Compare today's system vs the fixes, in-sample AND out-of-sample, with costs
python -m research.run

# 2. Does a higher timeframe let the edge clear costs?
python -m research.run_tf

# 3. Validate the ACTUAL shipped strategy class (live code == validated code)
python -m research.run_live_strategy
```

Data is real hourly/daily candles pulled from binance's public API (no keys)
and cached under `research/cache/`. Re-runs are instant.

## What the validation found (real numbers, binance, fees 7.5bps/side + slippage)

| Strategy / exit | OOS Profit Factor | OOS expectancy | Verdict |
|---|---|---|---|
| `vwap_macd` + legacy "strangle" exit (≈ the old live system) | **0.82** | −1.44 | losing — confirms the live logs |
| `vwap_macd` + fixed R-multiple exit | 0.76 | −1.92 | a good exit can't save a bad signal |
| `regime_trend` (daily, ADX) + R-multiple exit | **1.22** | +10.97 | **edge present, but only ~20 OOS trades** |
| `regime_trend` on 1h with costs | 1.00 | 0.00 | real signal, but costs eat it at this frequency |
| `regime_trend` on 4h with costs | 0.56 | −8.84 | chopped — higher TF is not automatically better |

**Conclusion:** the daily regime-filtered trend strategy is the only thing that
showed a cost-surviving out-of-sample edge. The sample is small (anecdotal, per
the skill's own threshold), so it ships **paper-only** and the live
`core/learning.py` layer will keep score and switch it off automatically if the
real record turns negative.

## Before adding any new strategy to the live agent

1. Write it as a `signal_fn(arr) -> {direction, atr}` in `research/strategies.py`.
2. Run it through `research/run.py` (IS/OOS, with costs).
3. It must show **Profit Factor > 1.0 out-of-sample, after costs.** If it
   doesn't, do **not** tune it until it does — that's curve-fitting. Change
   something structural (timeframe, regime filter, fewer assets) or drop it.
4. Only then port it to a `strategies/*.py` class and enable its flag.

## Files

- `data.py` — real OHLCV via ccxt, CSV-cached
- `engine.py` — event-driven backtester; decides on a closed bar, fills next bar's open (no look-ahead); charges fees + slippage both sides
- `exits.py` — `LegacyStrangleExit` (the old bleed) vs `RMultipleExit` (the fix)
- `strategies.py` — candidate signal generators
- `metrics.py` — full metric set + honest verdict (from the skill)
- `run.py`, `run_tf.py`, `run_live_strategy.py` — the harnesses above
