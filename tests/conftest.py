"""Shared fixtures: a synthetic OHLCV series and mock data sources (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indi_analyst.models import Fundamentals


def make_ohlcv(trend: str = "up", n: int = 300, start: float = 100.0) -> pd.DataFrame:
    """Deterministic synthetic OHLCV. `trend` in {up, down, flat}."""
    rng = np.random.default_rng(42)
    drift = {"up": 0.0015, "down": -0.0015, "flat": 0.0}[trend]
    returns = rng.normal(drift, 0.012, n)
    close = start * np.cumprod(1 + returns)
    dates = pd.bdate_range(end="2024-12-31", periods=n)
    high = close * (1 + rng.uniform(0.001, 0.01, n))
    low = close * (1 - rng.uniform(0.001, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


class MockPriceSource:
    """Injectable stand-in for YFinanceSource — no network."""

    def __init__(
        self,
        df: pd.DataFrame,
        fundamentals: Fundamentals | None = None,
        symbol: str = "TEST.NS",
        name: str = "Test Corp",
    ):
        self._df = df
        self._fund = fundamentals or Fundamentals(
            pe_ratio=22.0,
            roe=0.19,
            debt_to_equity=0.4,
            revenue_growth=0.18,
            profit_margin=0.16,
            sector="Testing",
        )
        self._symbol = symbol
        self._name = name

    def resolve(self, query: str):
        return self._symbol, self._name, "NSE"

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        return self._df

    def fundamentals(self, symbol: str) -> Fundamentals:
        return self._fund


@pytest.fixture
def uptrend_df() -> pd.DataFrame:
    return make_ohlcv("up")


@pytest.fixture
def downtrend_df() -> pd.DataFrame:
    return make_ohlcv("down")


@pytest.fixture
def mock_source(uptrend_df):
    return MockPriceSource(uptrend_df)
