"""Aggregate a set of trades / per-symbol results into performance statistics.

Every number here is derived from raw `Trade` fields so the maths stays auditable: win rate,
average win/loss, expectancy (mean R), profit factor, and the deepest drawdown of the pooled
equity curve. The buy-and-hold benchmark is the mean per-symbol close-to-close return over the
same window, so signal performance is always read against "just holding it".
"""

from __future__ import annotations

from statistics import mean

from indi_analyst.backtest.models import BacktestResult, BacktestStats, SymbolResult, Trade
from indi_analyst.backtest.simulator import parse_entry_actions
from indi_analyst.config import Settings, get_settings


def _max_drawdown(trades: list[Trade]) -> float:
    """Deepest peak-to-trough drop of an equity curve that compounds trades by entry date."""
    equity = peak = 1.0
    worst = 0.0
    for t in sorted(trades, key=lambda x: x.entry_date):
        equity *= 1 + t.return_pct
        peak = max(peak, equity)
        worst = min(worst, (equity - peak) / peak)
    return round(worst, 4)


def compute_stats(trades: list[Trade]) -> BacktestStats:
    """Performance stats for a flat list of trades (empty -> a zeroed BacktestStats)."""
    if not trades:
        return BacktestStats()
    wins = [t for t in trades if t.return_pct > 0]
    losses = [t for t in trades if t.return_pct <= 0]
    gross_win = sum(t.return_pct for t in wins)
    gross_loss = -sum(t.return_pct for t in losses)
    n = len(trades)
    return BacktestStats(
        trades=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / n, 4),
        avg_win_pct=round(mean(t.return_pct for t in wins), 4) if wins else None,
        avg_loss_pct=round(mean(t.return_pct for t in losses), 4) if losses else None,
        avg_return_pct=round(mean(t.return_pct for t in trades), 4),
        expectancy_r=round(mean(t.r_multiple for t in trades), 3),
        profit_factor=round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        max_drawdown=_max_drawdown(trades),
        avg_bars_held=round(mean(t.bars_held for t in trades), 1),
    )


def aggregate(
    results: list[SymbolResult],
    *,
    target: str,
    settings: Settings | None = None,
    warnings: list[str] | None = None,
) -> BacktestResult:
    """Roll per-symbol results up into a `BacktestResult` with overall + sliced stats."""
    settings = settings or get_settings()
    ok = [r for r in results if r.error is None]
    all_trades = [t for r in ok for t in r.trades]

    stats = compute_stats(all_trades)
    bh = [r.buy_hold_return for r in ok if r.buy_hold_return is not None]
    stats.buy_hold_return = round(mean(bh), 4) if bh else None

    by_action = {
        a.value: compute_stats([t for t in all_trades if t.entry_action == a])
        for a in sorted({t.entry_action for t in all_trades}, key=lambda a: a.value)
    }
    by_conviction = {
        c.value: compute_stats([t for t in all_trades if t.entry_conviction == c])
        for c in sorted({t.entry_conviction for t in all_trades}, key=lambda c: c.value)
    }

    notes = list(warnings or [])
    notes.extend(f"{r.symbol}: {r.error}" for r in results if r.error)

    return BacktestResult(
        target=target,
        period=settings.backtest_history_period,
        entry_actions=sorted(
            parse_entry_actions(settings.backtest_entry_actions), key=lambda a: a.value
        ),
        warmup_bars=settings.backtest_warmup_bars,
        max_hold_bars=settings.backtest_max_hold_bars,
        symbols=len(results),
        ok_symbols=len(ok),
        stats=stats,
        by_action=by_action,
        by_conviction=by_conviction,
        per_symbol=results,
        warnings=notes,
    )
