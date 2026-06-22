"""
research/ — honest validation toolkit for AgentEdge.

Nothing in here trades. It exists to answer one question before any
strategy is allowed near money: *does this have a real, out-of-sample,
cost-aware edge — or am I fooling myself?*

Pipeline:
    data.py     -> real OHLCV via ccxt (binance public), CSV-cached
    engine.py   -> event-driven backtester (no look-ahead), pluggable exits + costs
    exits.py    -> exit policies (legacy "strangle" vs the fixed R-multiple model)
    strategies.py -> candidate signal generators (incl. regime-filtered trend)
    metrics.py  -> the full metric set (PF, expectancy, Sharpe, break-even WR, ...)
    run.py      -> orchestrates IS/OOS comparison and prints the verdict table
"""
