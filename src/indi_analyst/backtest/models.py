"""Backtest vocabulary: individual trades, per-symbol results, and aggregate stats.

A `Trade` is one simulated long from an entry signal to its exit. Everything is stored raw
(prices, dates, R-multiple); interpretation (win/loss, expectancy) is derived in `metrics.py`.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from indi_analyst.models import Action, Conviction


class Trade(BaseModel):
    """One simulated long position, entry signal -> exit."""

    symbol: str
    entry_date: date
    entry_price: float  # fill at the bar AFTER the signal (next open) — never the signal bar
    exit_date: date
    exit_price: float
    exit_reason: str  # "target_1" | "stop" | "timeout"
    return_pct: float  # (exit - entry) / entry
    r_multiple: float  # (exit - entry) / (entry - stop) — reward in units of initial risk
    bars_held: int
    entry_action: Action  # the quant action that opened the trade
    entry_conviction: Conviction


class SymbolResult(BaseModel):
    """Outcome of backtesting a single symbol: its trades + a buy-and-hold benchmark."""

    symbol: str
    trades: list[Trade] = Field(default_factory=list)
    bars: int = 0  # bars available in the fetched history
    buy_hold_return: float | None = None  # close-to-close over the simulated window
    error: str | None = None  # set when the symbol could not be backtested


class BacktestStats(BaseModel):
    """Aggregate performance over a set of trades. All optional — empty when no trades."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None  # fraction of trades with a positive return
    avg_win_pct: float | None = None
    avg_loss_pct: float | None = None
    avg_return_pct: float | None = None
    expectancy_r: float | None = None  # mean R-multiple across all trades
    profit_factor: float | None = None  # gross win / gross loss
    max_drawdown: float | None = None  # deepest equity-curve drawdown (negative fraction)
    avg_bars_held: float | None = None
    buy_hold_return: float | None = None  # mean per-symbol buy-and-hold over the window


class BacktestResult(BaseModel):
    """Full backtest outcome: config echo + aggregate stats + per-symbol detail.

    Technical-only by construction: fundamentals/news are not point-in-time available from the
    free source, so the replayed score measures the technical timing signal alone.
    """

    target: str  # symbol or universe backtested
    period: str  # yfinance history period used
    entry_actions: list[Action]  # which actions opened trades
    warmup_bars: int
    max_hold_bars: int
    symbols: int = 0  # symbols attempted
    ok_symbols: int = 0  # symbols that produced a result (no fetch/sim error)

    stats: BacktestStats = Field(default_factory=BacktestStats)
    by_action: dict[str, BacktestStats] = Field(default_factory=dict)
    by_conviction: dict[str, BacktestStats] = Field(default_factory=dict)
    per_symbol: list[SymbolResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
