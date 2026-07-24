"""Build a StockSnapshot: fetch data + compute indicators. Deterministic, LLM-free."""

from __future__ import annotations

from statistics import mean

import pandas as pd

from indi_analyst.config import Settings, get_settings
from indi_analyst.datasources.factory import build_price_source
from indi_analyst.datasources.news import GoogleNewsSource
from indi_analyst.indicators import technical
from indi_analyst.models import StockSnapshot


def build_snapshot(
    query: str,
    *,
    settings: Settings | None = None,
    price_source=None,
    news_source=None,
) -> StockSnapshot:
    """Resolve a query to a full snapshot. Sources are injectable for testing."""
    settings = settings or get_settings()
    price_source = price_source or build_price_source(settings=settings)
    news_source = news_source if news_source is not None else GoogleNewsSource()

    warnings: list[str] = []

    symbol, name, exchange = price_source.resolve(query)
    df: pd.DataFrame = price_source.history(symbol, period=settings.history_period)

    if len(df) < 50:
        warnings.append(
            f"Only {len(df)} bars of history — longer-period indicators (SMA-200) may be unavailable."
        )

    technicals = technical.compute(df)
    fundamentals = price_source.fundamentals(symbol)

    news = []
    news_sentiment = None
    if news_source is not None:
        news = news_source.news(name or symbol, max_items=settings.news_max_items)
        sentiments = [n.sentiment for n in news if n.sentiment is not None]
        if sentiments:
            news_sentiment = round(mean(sentiments), 3)

    return StockSnapshot(
        symbol=symbol,
        query=query,
        name=name,
        exchange=exchange,
        technicals=technicals,
        fundamentals=fundamentals,
        news=news,
        news_sentiment=news_sentiment,
        warnings=warnings,
    )
