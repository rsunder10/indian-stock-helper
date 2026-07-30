"""Backtest orchestration: resolve a target to symbols, replay each, aggregate.

`target` is either a single symbol (``RELIANCE`` / ``TCS.NS``) or a universe the screener already
knows how to resolve (``nifty50``, ``watchlist:...``, ``file:...``). History is fetched once per
symbol through the shared, rate-limited price source; per-symbol failures are isolated exactly like
`screener.batch.scan_universe`, so one bad ticker never sinks the run.
"""

from __future__ import annotations

from typing import Callable

from indi_analyst.backtest.metrics import aggregate
from indi_analyst.backtest.models import BacktestResult, SymbolResult
from indi_analyst.backtest.simulator import simulate_symbol
from indi_analyst.config import Settings, get_settings
from indi_analyst.datasources.factory import build_price_source
from indi_analyst.screener.universe import INDEX_UNIVERSES, _to_yf_symbol, load_universe

ProgressCb = Callable[[int, int, str], None]


def _resolve_symbols(target: str, *, settings: Settings, warnings: list[str]) -> list[str]:
    """Expand a target into yfinance symbols: a universe name, or a single normalized ticker."""
    key = target.strip().lower()
    if key in INDEX_UNIVERSES or key.startswith("watchlist:") or key.startswith("file:"):
        members = load_universe(target, settings=settings, warnings=warnings)
        return [m.symbol for m in members]
    return [_to_yf_symbol(target)]


def run_backtest(
    target: str,
    *,
    settings: Settings | None = None,
    price_source=None,
    limit: int | None = None,
    on_progress: ProgressCb | None = None,
) -> BacktestResult:
    """Backtest ``target`` (symbol or universe) and return aggregate + per-symbol results.

    `price_source` is injectable for network-free tests. `limit` caps the symbol count.
    """
    settings = settings or get_settings()
    price_source = price_source or build_price_source(settings=settings)

    warnings: list[str] = []
    symbols = _resolve_symbols(target, settings=settings, warnings=warnings)
    if limit is not None:
        symbols = symbols[:limit]

    results: list[SymbolResult] = []
    total = len(symbols)
    for done, sym in enumerate(symbols, 1):
        try:
            df = price_source.history(sym, period=settings.backtest_history_period)
            result = simulate_symbol(df, symbol=sym, settings=settings)
        except Exception as e:  # bad ticker, no history — isolate it
            result = SymbolResult(symbol=sym, error=str(e))
        results.append(result)
        if on_progress is not None:
            on_progress(done, total, sym)

    return aggregate(results, target=target, settings=settings, warnings=warnings)
