"""Corporate-action history: parsing, the dividend-consistency valuation gate, and snapshot wiring.

All offline — the parser is exercised with synthetic frames and the source with a fake Ticker.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from indi_analyst.analysis.valuation import compute_valuation
from indi_analyst.config import Settings
from indi_analyst.datasources import throttle, yfinance_source
from indi_analyst.datasources.yfinance_source import _corporate_actions, _split_ratio
from indi_analyst.indicators import technical
from indi_analyst.models import CorporateActions, Fundamentals, StockSnapshot
from tests.conftest import MockPriceSource, make_ohlcv


def _actions(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """Build a yfinance-shaped actions frame from (date, dividend, split) rows."""
    return pd.DataFrame(
        {"Dividends": [r[1] for r in rows], "Stock Splits": [r[2] for r in rows]},
        index=pd.to_datetime([r[0] for r in rows]),
    )


# --- parser -----------------------------------------------------------------


def test_split_ratio_formats_forward_and_reverse():
    assert _split_ratio(2.0) == "2:1"
    assert _split_ratio(0.2) == "1:5"  # reverse split
    assert _split_ratio(1.0) is None  # a no-op split isn't reported
    assert _split_ratio(0.0) is None


def test_parser_counts_paying_years_and_last_dividend():
    frame = _actions([
        ("2020-05-01", 5.0, 0.0),
        ("2021-05-01", 5.5, 0.0),
        ("2022-05-01", 6.0, 0.0),
        ("2023-05-01", 6.5, 0.0),
        ("2024-05-01", 7.0, 0.0),
    ])
    ca = _corporate_actions(frame, as_of=date(2024, 12, 31), lookback_years=6, split_recency_days=365)
    assert ca is not None
    assert ca.dividend_paying_years == 5
    assert ca.lookback_years == 6
    assert ca.last_dividend == 7.0
    assert ca.last_dividend_date == date(2024, 5, 1)
    assert ca.last_split_ratio is None
    assert ca.recent_split is None


def test_parser_windows_out_old_dividend_years():
    frame = _actions([
        ("2015-05-01", 5.0, 0.0),  # outside a 6y window ending 2024
        ("2016-05-01", 5.0, 0.0),  # outside
        ("2023-05-01", 6.0, 0.0),
        ("2024-05-01", 7.0, 0.0),
    ])
    ca = _corporate_actions(frame, as_of=date(2024, 12, 31), lookback_years=6, split_recency_days=365)
    assert ca.dividend_paying_years == 2  # only 2023 + 2024 fall in-window


def test_parser_flags_recent_split_and_ratio():
    recent = _corporate_actions(
        _actions([("2024-06-01", 0.0, 2.0)]),
        as_of=date(2024, 12, 31), lookback_years=6, split_recency_days=365,
    )
    assert recent.last_split_ratio == "2:1"
    assert recent.last_split_date == date(2024, 6, 1)
    assert recent.recent_split is True

    old = _corporate_actions(
        _actions([("2019-06-01", 0.0, 2.0)]),
        as_of=date(2024, 12, 31), lookback_years=6, split_recency_days=365,
    )
    assert old.recent_split is False


def test_parser_returns_none_for_empty_history():
    assert _corporate_actions(pd.DataFrame(), as_of=None, lookback_years=6, split_recency_days=365) is None
    assert _corporate_actions(None, as_of=None, lookback_years=6, split_recency_days=365) is None


def test_source_corporate_actions_degrades_to_none(monkeypatch):
    monkeypatch.setattr(throttle.time, "sleep", lambda s: None)

    class Boom:
        @property
        def actions(self):
            raise RuntimeError("network blip")

    monkeypatch.setattr(yfinance_source.yf, "Ticker", lambda symbol: Boom())
    assert yfinance_source.YFinanceSource(retries=1).corporate_actions("RELIANCE.NS") is None


# --- valuation gate ---------------------------------------------------------


def _snap(fund: Fundamentals, ca: CorporateActions | None = None) -> StockSnapshot:
    df = make_ohlcv("up", n=300)
    return StockSnapshot(
        symbol="TEST.NS", query="TEST", technicals=technical.compute(df),
        fundamentals=fund, corporate_actions=ca,
    )


def test_ddm_skipped_for_inconsistent_payer():
    # A positive yield but only one paying year -> the Gordon model is dropped, with a reason.
    fund = Fundamentals(dividend_yield=0.03, earnings_growth=0.04)
    ca = CorporateActions(dividend_paying_years=1, lookback_years=6)
    val = compute_valuation(_snap(fund, ca), Settings())
    assert [m.name for m in val.methods] == []  # nothing else could run either
    assert val.fair_value is None
    assert any("Dividend discount skipped" in r for r in val.reasons)


def test_ddm_runs_and_detail_enriched_for_consistent_payer():
    fund = Fundamentals(dividend_yield=0.03, earnings_growth=0.04)
    ca = CorporateActions(dividend_paying_years=5, lookback_years=6)
    val = compute_valuation(_snap(fund, ca), Settings())
    dd = next(m for m in val.methods if m.name == "Dividend discount")
    assert "paid in 5 of last 6 yrs" in dd.detail


def test_ddm_unchanged_when_no_corporate_actions():
    # Regression guard: with no corporate-action history the gate is inactive and the DDM runs.
    fund = Fundamentals(dividend_yield=0.03, earnings_growth=0.04)
    val = compute_valuation(_snap(fund, None), Settings())
    assert [m.name for m in val.methods] == ["Dividend discount"]


# --- snapshot wiring --------------------------------------------------------


class _ActionsSource(MockPriceSource):
    def __init__(self, df, ca):
        super().__init__(df)
        self._ca = ca

    def corporate_actions(self, symbol, *, as_of=None, lookback_years=6, split_recency_days=365):
        return self._ca


def test_snapshot_pulls_corporate_actions_and_warns_on_recent_split():
    from indi_analyst.analysis.snapshot import build_snapshot

    ca = CorporateActions(
        dividend_paying_years=4, lookback_years=6,
        last_split_ratio="2:1", last_split_date=date(2024, 6, 1), recent_split=True,
    )
    src = _ActionsSource(make_ohlcv("up"), ca)
    snap = build_snapshot("TEST", price_source=src, news_source=None)

    assert snap.corporate_actions is ca
    assert any("2:1 stock split" in w for w in snap.warnings)


def test_snapshot_without_corporate_actions_method_is_none():
    from indi_analyst.analysis.snapshot import build_snapshot

    # MockPriceSource has no corporate_actions method -> snapshot leaves the field None, no error.
    snap = build_snapshot("TEST", price_source=MockPriceSource(make_ohlcv("up")), news_source=None)
    assert snap.corporate_actions is None
