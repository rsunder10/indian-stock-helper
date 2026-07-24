"""Trade-level math: stop below entry, targets ordered, risk-reward sane."""

from __future__ import annotations

from indi_analyst.analysis.levels import compute_levels
from indi_analyst.config import Settings
from indi_analyst.indicators import technical
from indi_analyst.models import StockSnapshot
from tests.conftest import make_ohlcv


def _snapshot(trend: str) -> StockSnapshot:
    df = make_ohlcv(trend, n=300)
    return StockSnapshot(symbol="TEST.NS", query="TEST", technicals=technical.compute(df))


def test_stop_below_entry_targets_above():
    lv = compute_levels(_snapshot("up"), Settings())
    assert lv.stop_loss < lv.entry_mid
    assert lv.target_1 > lv.entry_mid
    assert lv.target_2 > lv.target_1
    assert lv.stop_loss_pct < 0
    assert lv.target_1_pct > 0


def test_risk_reward_positive_and_reasonable():
    lv = compute_levels(_snapshot("up"), Settings())
    assert lv.risk_reward > 0
    # target1 is set at ~2R by default, so R:R should be roughly >= 1.5
    assert lv.risk_reward >= 1.0


def test_atr_multiple_respected():
    settings = Settings(atr_stop_multiple=2.5)
    lv = compute_levels(_snapshot("flat"), settings)
    assert lv.atr_multiple == 2.5


def test_levels_finite_for_downtrend():
    lv = compute_levels(_snapshot("down"), Settings())
    assert lv.stop_loss < lv.entry_mid < lv.target_1 < lv.target_2
