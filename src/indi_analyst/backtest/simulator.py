"""Walk-forward trade simulation for one symbol.

At each bar the deterministic pipeline is replayed (`snapshot_at` -> `score` -> `compute_levels`).
When the quant action is an entry action and we are flat, a long is opened at the *next* bar's
open (never the signal bar's close — that would be look-ahead). The position is then walked
forward bar by bar until the stop or the first target is touched, or a max-hold timeout forces a
close. One position at a time; scanning resumes only after the trade exits.
"""

from __future__ import annotations

import pandas as pd

from indi_analyst.analysis.levels import compute_levels
from indi_analyst.analysis.scoring import score
from indi_analyst.backtest.models import SymbolResult, Trade
from indi_analyst.backtest.replay import snapshot_at
from indi_analyst.config import Settings, get_settings
from indi_analyst.models import Action


def parse_entry_actions(raw: str) -> set[Action]:
    """Parse a comma list like ``"BUY,ACCUMULATE"`` into a set of `Action`s.

    Unknown tokens are ignored; an empty result falls back to BUY/ACCUMULATE so a bad config
    value never silently disables every entry.
    """
    out: set[Action] = set()
    for tok in raw.split(","):
        tok = tok.strip().upper()
        if not tok:
            continue
        try:
            out.add(Action(tok))
        except ValueError:
            continue
    return out or {Action.BUY, Action.ACCUMULATE}


def resolve_exit(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    *,
    entry_idx: int,
    stop: float,
    target: float,
    max_hold: int,
) -> tuple[int, float, str]:
    """Walk forward from ``entry_idx`` and return ``(exit_idx, exit_price, reason)``.

    On any bar, if the low pierces the stop we exit at the stop; else if the high reaches the
    target we exit at the target. When both happen on the same bar we assume the **stop** hit
    first (conservative — we never credit an ambiguous bar as a win). If neither triggers within
    ``max_hold`` bars we exit at that bar's close ("timeout").
    """
    n = len(closes)
    last = min(entry_idx + max_hold, n - 1)
    for j in range(entry_idx, last + 1):
        if float(lows.iloc[j]) <= stop:
            return j, stop, "stop"
        if float(highs.iloc[j]) >= target:
            return j, target, "target_1"
    return last, float(closes.iloc[last]), "timeout"


def simulate_symbol(
    df: pd.DataFrame,
    *,
    symbol: str = "BACKTEST",
    settings: Settings | None = None,
) -> SymbolResult:
    """Backtest a single symbol's OHLCV history, returning its trades + buy-and-hold benchmark."""
    settings = settings or get_settings()
    entry_actions = parse_entry_actions(settings.backtest_entry_actions)
    warmup = settings.backtest_warmup_bars
    max_hold = settings.backtest_max_hold_bars

    n = len(df)
    if n < warmup + 2:
        return SymbolResult(symbol=symbol, bars=n, error=f"Too few bars ({n}) for warmup {warmup}.")

    opens, highs, lows, closes = df["Open"], df["High"], df["Low"], df["Close"]
    index = df.index
    trades: list[Trade] = []

    i = warmup
    while i < n - 1:  # need bar i+1 to exist for the entry fill
        snap = snapshot_at(df, i, symbol)
        quant = score(snap, settings)
        if quant.action not in entry_actions:
            i += 1
            continue

        levels = compute_levels(snap, settings)
        entry_idx = i + 1
        entry_price = float(opens.iloc[entry_idx])
        stop, target = levels.stop_loss, levels.target_1

        # Skip unfillable/degenerate setups: the next open already gapped through the stop or the
        # target, so there is no honest long to model.
        risk = entry_price - stop
        if risk <= 0 or entry_price >= target:
            i += 1
            continue

        exit_idx, exit_price, reason = resolve_exit(
            highs, lows, closes, entry_idx=entry_idx, stop=stop, target=target, max_hold=max_hold
        )
        ret = (exit_price - entry_price) / entry_price
        trades.append(
            Trade(
                symbol=symbol,
                entry_date=index[entry_idx].date(),
                entry_price=round(entry_price, 2),
                exit_date=index[exit_idx].date(),
                exit_price=round(exit_price, 2),
                exit_reason=reason,
                return_pct=round(ret, 4),
                r_multiple=round((exit_price - entry_price) / risk, 2),
                bars_held=exit_idx - entry_idx,
                entry_action=quant.action,
                entry_conviction=quant.conviction,
            )
        )
        i = exit_idx + 1  # one position at a time — resume after the exit bar

    buy_hold = float((closes.iloc[-1] - closes.iloc[warmup]) / closes.iloc[warmup])
    return SymbolResult(symbol=symbol, trades=trades, bars=n, buy_hold_return=round(buy_hold, 4))
