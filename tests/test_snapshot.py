"""End-to-end engine test over a mocked source — no network, rule-based provider."""

from __future__ import annotations

from datetime import UTC, datetime

from indi_analyst.analysis import snapshot as snapshot_module
from indi_analyst.analysis.engine import analyze
from indi_analyst.analysis.snapshot import build_snapshot
from indi_analyst.config import Settings
from indi_analyst.models import Action, Conviction, Recommendation
from tests.conftest import MockPriceSource, make_ohlcv


def _analyze(trend: str) -> Recommendation:
    source = MockPriceSource(make_ohlcv(trend, n=300))
    settings = Settings(default_llm_provider="rulebased")
    # news_source=None -> skip network news fetch
    return analyze(
        "TEST",
        provider="rulebased",
        settings=settings,
        price_source=source,
        news_source=None,
    )


def test_engine_produces_well_formed_recommendation():
    rec = _analyze("up")
    assert isinstance(rec, Recommendation)
    assert rec.provider == "rulebased"
    assert isinstance(rec.action, Action)
    assert isinstance(rec.conviction, Conviction)
    assert 0 <= rec.quant.score <= 100
    # levels present and ordered
    assert rec.levels.stop_loss < rec.levels.entry_mid < rec.levels.target_1
    # verdict populated
    assert rec.verdict.summary
    assert rec.verdict.thesis
    assert rec.disclaimer


def test_uptrend_more_bullish_than_downtrend():
    up = _analyze("up")
    down = _analyze("down")
    assert up.quant.score > down.quant.score


def test_downtrend_not_a_buy():
    down = _analyze("down")
    assert down.action in {Action.HOLD, Action.AVOID, Action.SELL}


def test_snapshot_carries_data_quality_metadata():
    # A source whose history() sets df.attrs (as the hardened YFinanceSource does).
    df = make_ohlcv("up", n=300)
    df.attrs["warnings"] = ["Dropped 2 bar(s) with non-positive prices."]
    df.attrs["source"] = "yfinance"
    df.attrs["as_of"] = datetime(2026, 1, 5, tzinfo=UTC)

    snap = build_snapshot(
        "TEST",
        settings=Settings(default_llm_provider="rulebased"),
        price_source=MockPriceSource(df),
        news_source=None,
    )
    assert snap.data_source == "yfinance"
    assert snap.data_as_of == datetime(2026, 1, 5, tzinfo=UTC)
    assert "Dropped 2 bar(s) with non-positive prices." in snap.warnings


def test_snapshot_without_attrs_still_builds():
    # A plain mock with no attrs must not break the merge (regression guard).
    snap = build_snapshot(
        "TEST",
        settings=Settings(default_llm_provider="rulebased"),
        price_source=MockPriceSource(make_ohlcv("up", n=300)),
        news_source=None,
    )
    assert snap.data_source is None
    assert snap.data_as_of is None


def test_explicit_none_news_source_skips_live_news(monkeypatch):
    def fail_if_constructed():
        raise AssertionError("default news source should not be constructed")

    monkeypatch.setattr(snapshot_module, "GoogleNewsSource", fail_if_constructed)
    snap = build_snapshot(
        "TEST",
        settings=Settings(default_llm_provider="rulebased"),
        price_source=MockPriceSource(make_ohlcv("up", n=300)),
        news_source=None,
    )

    assert snap.news == []
    assert snap.news_sentiment is None


def test_news_failure_degrades_to_a_warning():
    class BrokenNews:
        def news(self, name_or_symbol, max_items):
            raise RuntimeError("news service unavailable")

    snap = build_snapshot(
        "TEST",
        settings=Settings(default_llm_provider="rulebased"),
        price_source=MockPriceSource(make_ohlcv("up", n=300)),
        news_source=BrokenNews(),
    )

    assert snap.news == []
    assert any("News unavailable" in warning for warning in snap.warnings)
