"""Tests for the free yfinance baseline source."""

from __future__ import annotations

import numpy as np
import pandas as pd

from indi_analyst.datasources import yfinance_source


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

    monkeypatch.setattr(yfinance_source.yf, "Ticker", lambda symbol: FakeTicker())

    out = yfinance_source.YFinanceSource().history("RELIANCE.NS")

    assert len(out) == 1
    assert out["Close"].iloc[-1] == 101.0
