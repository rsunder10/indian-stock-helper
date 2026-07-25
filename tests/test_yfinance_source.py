"""Tests for the free yfinance baseline source: validation, provenance, and retry."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indi_analyst.datasources import throttle, yfinance_source


def _install_ticker(monkeypatch, ticker) -> None:
    monkeypatch.setattr(yfinance_source.yf, "Ticker", lambda symbol: ticker)


def _no_sleep(monkeypatch) -> None:
    """Retry/limiter backoff lives in the throttle module — silence its sleeps."""
    monkeypatch.setattr(throttle.time, "sleep", lambda s: None)


def test_history_drops_invalid_close_rows(monkeypatch):
    raw = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, np.nan],
            "Volume": [1000, 1100],
        },
        index=pd.date_range("2026-01-01", periods=2),
    )

    class FakeTicker:
        def history(self, **kwargs):
            return raw

    _install_ticker(monkeypatch, FakeTicker())
    out = yfinance_source.YFinanceSource().history("RELIANCE.NS")

    assert len(out) == 1
    assert out["Close"].iloc[-1] == 101.0


def test_history_validates_and_warns(monkeypatch):
    # Row 0: clean. Row 1: High < Low (inconsistent). Row 2: negative price. Row 3: clean.
    raw = pd.DataFrame(
        {
            "Open": [100.0, 101.0, -5.0, 104.0],
            "High": [102.0, 100.0, 106.0, 106.0],  # row1 High(100) < Low(105)
            "Low": [99.0, 105.0, 103.0, 103.0],
            "Close": [101.0, 102.0, 104.0, 105.0],
            "Volume": [1000, 1100, 1200, 1300],
        },
        index=pd.date_range("2026-01-01", periods=4),
    )

    class FakeTicker:
        def history(self, **kwargs):
            return raw

    _install_ticker(monkeypatch, FakeTicker())
    out = yfinance_source.YFinanceSource().history("RELIANCE.NS")

    # Only the two clean bars survive.
    assert list(out["Close"]) == [101.0, 105.0]
    warnings = out.attrs["warnings"]
    assert any("non-positive" in w for w in warnings)
    assert any("inconsistent OHLC" in w for w in warnings)


def test_history_resorts_out_of_order_dates(monkeypatch):
    idx = pd.to_datetime(["2026-01-03", "2026-01-01", "2026-01-02"])
    raw = pd.DataFrame(
        {
            "Open": [102.0, 100.0, 101.0],
            "High": [103.0, 101.0, 102.0],
            "Low": [101.0, 99.0, 100.0],
            "Close": [102.5, 100.5, 101.5],
            "Volume": [1200, 1000, 1100],
        },
        index=idx,
    )

    class FakeTicker:
        def history(self, **kwargs):
            return raw

    _install_ticker(monkeypatch, FakeTicker())
    out = yfinance_source.YFinanceSource().history("RELIANCE.NS")

    assert out.index.is_monotonic_increasing
    assert any("out of order" in w for w in out.attrs["warnings"])


def test_history_records_source_and_as_of(monkeypatch):
    idx = pd.date_range("2026-01-01", periods=3)
    raw = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [102.0, 103.0, 104.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [101.0, 102.0, 103.0],
            "Volume": [1000, 1100, 1200],
        },
        index=idx,
    )

    class FakeTicker:
        def history(self, **kwargs):
            return raw

    _install_ticker(monkeypatch, FakeTicker())
    out = yfinance_source.YFinanceSource().history("RELIANCE.NS")

    assert out.attrs["source"] == "yfinance"
    assert out.attrs["as_of"] == idx[-1].to_pydatetime()
    assert out.attrs["warnings"] == []  # clean data, no notes


def test_history_retries_transient_failure(monkeypatch):
    good = pd.DataFrame(
        {
            "Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0], "Volume": [1000],
        },
        index=pd.date_range("2026-01-01", periods=1),
    )

    class FlakyTicker:
        def __init__(self):
            self.calls = 0

        def history(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("blip")
            return good

    ticker = FlakyTicker()
    _install_ticker(monkeypatch, ticker)
    _no_sleep(monkeypatch)

    src = yfinance_source.YFinanceSource(retries=3, backoff=0.01)
    out = src.history("RELIANCE.NS")
    assert len(out) == 1
    assert ticker.calls == 3


def test_history_raises_after_exhausting_retries(monkeypatch):
    class DeadTicker:
        def __init__(self):
            self.calls = 0

        def history(self, **kwargs):
            self.calls += 1
            raise ConnectionError("down")

    ticker = DeadTicker()
    _install_ticker(monkeypatch, ticker)
    _no_sleep(monkeypatch)

    src = yfinance_source.YFinanceSource(retries=2, backoff=0.01)
    with pytest.raises(ConnectionError):
        src.history("RELIANCE.NS")
    assert ticker.calls == 2


def test_safe_info_degrades_to_empty_after_retries(monkeypatch):
    class BadInfoTicker:
        @property
        def info(self):
            raise RuntimeError("no info")

    _install_ticker(monkeypatch, BadInfoTicker())
    _no_sleep(monkeypatch)

    fund = yfinance_source.YFinanceSource(retries=2, backoff=0.01).fundamentals("RELIANCE.NS")
    assert fund.pe_ratio is None  # empty Fundamentals, no crash
