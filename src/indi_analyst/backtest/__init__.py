"""Walk-forward backtesting of the deterministic signal + trade levels (roadmap Phase 4).

Replays the real pipeline (`snapshot_at` -> `score` -> `compute_levels`) over historical price
data to measure how the BUY/ACCUMULATE signals and ATR-based levels would have performed. It is
technical-only by design: fundamentals/news are not point-in-time available from the free source,
so using them would be look-ahead bias. See docs/methodology.md ("Backtesting").
"""

from __future__ import annotations

from indi_analyst.backtest.engine import run_backtest
from indi_analyst.backtest.metrics import aggregate, compute_stats
from indi_analyst.backtest.models import BacktestResult, BacktestStats, SymbolResult, Trade
from indi_analyst.backtest.replay import snapshot_at
from indi_analyst.backtest.simulator import resolve_exit, simulate_symbol

__all__ = [
    "BacktestResult",
    "BacktestStats",
    "SymbolResult",
    "Trade",
    "aggregate",
    "compute_stats",
    "resolve_exit",
    "run_backtest",
    "simulate_symbol",
    "snapshot_at",
]
