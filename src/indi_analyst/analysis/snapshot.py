"""Build a StockSnapshot: fetch data + compute indicators. Deterministic, LLM-free."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from indi_analyst.analysis.macro import resolve_macro_signals
from indi_analyst.config import Settings, get_settings
from indi_analyst.datasources.factory import build_price_source
from indi_analyst.datasources.news import GoogleNewsSource, aggregate_sentiment
from indi_analyst.indicators import technical
from indi_analyst.models import Fundamentals, StockSnapshot

DEFAULT_NEWS_SOURCE = object()


def build_snapshot(
    query: str,
    *,
    settings: Settings | None = None,
    price_source=None,
    news_source=DEFAULT_NEWS_SOURCE,
) -> StockSnapshot:
    """Resolve a query to a full snapshot. Sources are injectable for testing."""
    settings = settings or get_settings()
    price_source = price_source or build_price_source(settings=settings)
    if news_source is DEFAULT_NEWS_SOURCE:
        news_source = GoogleNewsSource()

    warnings: list[str] = []

    symbol, name, exchange = price_source.resolve(query)
    df: pd.DataFrame = price_source.history(symbol, period=settings.history_period)

    # Data-quality warnings + provenance ride along on df.attrs (set by the source at the
    # network boundary). Read them here, before any slicing, since attrs isn't guaranteed to
    # survive later pandas ops. A mock source without attrs yields empty defaults.
    warnings.extend(df.attrs.get("warnings", []))
    data_source = df.attrs.get("source")
    data_as_of = df.attrs.get("as_of")

    if len(df) < 50:
        warnings.append(
            f"Only {len(df)} bars of history — longer-period indicators (SMA-200) may be unavailable."
        )

    technicals = technical.compute(df)
    get_fundamentals = getattr(price_source, "fundamentals", None)
    if callable(get_fundamentals):
        try:
            fundamentals = get_fundamentals(symbol)
        except Exception as exc:  # source-specific failure; preserve technical analysis
            fundamentals = Fundamentals()
            warnings.append(f"Fundamentals unavailable: {exc}")
    else:
        fundamentals = Fundamentals()
        warnings.append("Fundamentals unavailable — the price source has no fundamentals adapter.")

    # Macro overlays (budget, RBI rate cycle, …): deterministic, from bundled packs — no network.
    # Keyed on the stock's sector; unmapped/None sectors or disabled/absent packs simply contribute
    # nothing. The backtest builds snapshots without a sector, so all overlays stay inert there.
    macro_signals = resolve_macro_signals(fundamentals.sector, settings)

    # Corporate actions (dividend/split history) are optional: sources that don't expose them
    # simply leave `corporate_actions` None. `as_of` ties split-recency to the data's freshness
    # so the result is deterministic rather than clock-dependent.
    corporate_actions = None
    get_actions = getattr(price_source, "corporate_actions", None)
    if callable(get_actions):
        try:
            corporate_actions = get_actions(
                symbol,
                as_of=data_as_of.date() if isinstance(data_as_of, datetime) else None,
                lookback_years=settings.corporate_action_lookback_years,
                split_recency_days=settings.split_recency_days,
            )
        except Exception:
            corporate_actions = None
    if corporate_actions and corporate_actions.recent_split:
        ratio = corporate_actions.last_split_ratio or "recent"
        when = corporate_actions.last_split_date
        warnings.append(
            f"Recent {ratio} stock split"
            + (f" on {when:%Y-%m-%d}" if when else "")
            + " — pre-split price levels may look discontinuous."
        )

    news = []
    news_sentiment = None
    if news_source is not None and settings.news_max_items > 0:
        try:
            news = news_source.news(name or symbol, max_items=settings.news_max_items)
            news_sentiment = aggregate_sentiment(
                news, halflife_days=settings.news_recency_halflife_days
            )
            if len(news) < 3:
                warnings.append(f"Thin news coverage — only {len(news)} headline(s) found.")
            else:
                freshest = max(
                    (n.published for n in news if n.published is not None),
                    default=None,
                )
                if freshest is not None:
                    age_days = (datetime.now(timezone.utc) - freshest).days
                    if age_days > 14:
                        warnings.append(f"Stale news — freshest headline is {age_days} days old.")
        except Exception as exc:  # news is optional; never lose the deterministic price analysis
            warnings.append(f"News unavailable: {exc}")

    seed_macro = sorted({signal.label for signal in macro_signals if signal.fetched_at is None})
    if seed_macro:
        warnings.append(
            "Macro overlay pack(s) are unrefreshed seed data: "
            + ", ".join(seed_macro)
            + ". Refresh the relevant pack before relying on its magnitude."
        )

    return StockSnapshot(
        symbol=symbol,
        query=query,
        name=name,
        exchange=exchange,
        technicals=technicals,
        fundamentals=fundamentals,
        corporate_actions=corporate_actions,
        macro_signals=macro_signals,
        news=news,
        news_sentiment=news_sentiment,
        warnings=warnings,
        data_source=data_source,
        data_as_of=data_as_of,
    )
