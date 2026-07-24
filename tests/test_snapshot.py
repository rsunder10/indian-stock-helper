"""End-to-end engine test over a mocked source — no network, rule-based provider."""

from __future__ import annotations

from indi_analyst.analysis.engine import analyze
from indi_analyst.config import Settings
from indi_analyst.models import Action, Conviction, Recommendation
from tests.conftest import MockPriceSource, make_ohlcv


def _analyze(trend: str) -> Recommendation:
    source = MockPriceSource(make_ohlcv(trend, n=300))
    settings = Settings(default_llm_provider="rulebased")
    # news_source=None -> skip network news fetch
    return analyze(
        "TEST", provider="rulebased", settings=settings,
        price_source=source, news_source=None,
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
