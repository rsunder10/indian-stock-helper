"""Point-in-time snapshot replay — the look-ahead-free core of the backtester.

`technical.compute` reads only trailing windows and the last bar, so computing it on
``df.iloc[: i + 1]`` yields exactly the snapshot an analyst would have had at bar ``i`` —
appending future bars cannot change it (guarded by a regression test). Fundamentals and news
are deliberately left empty: the free source only exposes *current* values, and backfilling
them into history would be look-ahead bias.
"""

from __future__ import annotations

import pandas as pd

from indi_analyst.indicators import technical
from indi_analyst.models import Fundamentals, StockSnapshot


def snapshot_at(df: pd.DataFrame, i: int, symbol: str = "BACKTEST") -> StockSnapshot:
    """Build the deterministic snapshot as of bar ``i`` using only rows ``0..i``.

    Technical-only: `fundamentals` is empty and `news_sentiment` is None, so the downstream
    score reflects the technical signal alone (see module docstring).
    """
    window = df.iloc[: i + 1]
    technicals = technical.compute(window)
    as_of = window.index[-1]
    return StockSnapshot(
        symbol=symbol,
        query=symbol,
        technicals=technicals,
        fundamentals=Fundamentals(),
        news=[],
        news_sentiment=None,
        data_as_of=as_of.to_pydatetime() if hasattr(as_of, "to_pydatetime") else None,
    )
