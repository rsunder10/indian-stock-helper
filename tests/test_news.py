"""News source tests — recency-weighted sentiment, dedupe, freshest-first ordering. No network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from indi_analyst.datasources import news as news_mod
from indi_analyst.datasources.news import GoogleNewsSource, aggregate_sentiment
from indi_analyst.models import NewsItem

NOW = datetime(2026, 7, 26, tzinfo=UTC)


def _item(title, sentiment, age_days=None):
    published = None if age_days is None else NOW - timedelta(days=age_days)
    return NewsItem(title=title, sentiment=sentiment, published=published)


# --- aggregate_sentiment ----------------------------------------------------


def test_aggregate_none_when_no_scored_items():
    assert aggregate_sentiment([]) is None
    assert aggregate_sentiment([_item("x", None, 1)]) is None


def test_aggregate_flat_mean_without_timestamps():
    items = [_item("a", 1.0), _item("b", -1.0)]  # no published dates -> unit weights
    assert aggregate_sentiment(items, now=NOW) == 0.0


def test_aggregate_zero_halflife_is_plain_mean():
    items = [_item("a", 1.0, age_days=0), _item("b", -1.0, age_days=30)]
    assert aggregate_sentiment(items, halflife_days=0, now=NOW) == 0.0


def test_recent_headline_dominates_older_one():
    # Fresh positive vs old negative: with decay the aggregate leans positive.
    items = [_item("fresh good", 1.0, age_days=0), _item("old bad", -1.0, age_days=21)]
    agg = aggregate_sentiment(items, halflife_days=7.0, now=NOW)
    assert agg is not None and agg > 0.5  # 21d = 3 halflives -> old weight ~0.125


def test_recency_weighting_orders_correctly():
    # Same magnitudes, but swapping which is fresh flips the sign of the aggregate.
    good_fresh = aggregate_sentiment(
        [_item("g", 1.0, 0), _item("b", -1.0, 14)], halflife_days=7.0, now=NOW
    )
    bad_fresh = aggregate_sentiment(
        [_item("g", 1.0, 14), _item("b", -1.0, 0)], halflife_days=7.0, now=NOW
    )
    assert good_fresh is not None and bad_fresh is not None
    assert good_fresh > 0 > bad_fresh


# --- GoogleNewsSource dedupe + ordering (fake _fetch, no network) -----------


def test_news_dedupes_and_sorts_freshest_first(monkeypatch):
    batch_a = [
        _item("Reliance jumps on results", 0.5, age_days=1),
        _item("Reliance jumps on results", 0.5, age_days=1),  # exact dup
    ]
    batch_b = [
        _item("  reliance JUMPS on results  ", 0.5, age_days=1),  # normalized dup
        _item("Analysts upgrade Reliance", 0.3, age_days=0),  # fresher, unique
    ]

    calls: list[str] = []

    def fake_fetch(query: str):
        calls.append(query)
        return batch_a if "stock" in query else batch_b

    monkeypatch.setattr(news_mod, "_fetch", fake_fetch)

    got = GoogleNewsSource().news("Reliance", max_items=8)

    assert len(calls) == 2  # multi-query
    titles = [n.title for n in got]
    assert len(titles) == 2  # duplicates collapsed
    assert titles[0] == "Analysts upgrade Reliance"  # freshest first


def test_news_empty_term_returns_empty():
    assert GoogleNewsSource().news("   ") == []
