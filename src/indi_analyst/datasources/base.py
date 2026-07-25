"""Protocols for pluggable data sources.

Keeping these as typing.Protocol lets the analysis layer depend on the shape, not the
    concrete yfinance implementation — so a mocked or future free source drops in without
    touching the engine.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from indi_analyst.models import Fundamentals, NewsItem


class PriceSource(Protocol):
    def resolve(self, query: str) -> tuple[str, str | None, str | None]:
        """Map a user query to (symbol, display_name, exchange)."""
        ...

    def history(self, symbol: str, period: str) -> pd.DataFrame:
        """OHLCV DataFrame indexed by date with columns Open/High/Low/Close/Volume."""
        ...


class FundamentalsSource(Protocol):
    def fundamentals(self, symbol: str) -> Fundamentals: ...


class NewsSource(Protocol):
    def news(self, name_or_symbol: str, max_items: int) -> list[NewsItem]: ...
