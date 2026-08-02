"""Indicator correctness on known / synthetic series."""

from __future__ import annotations

import numpy as np
import pandas as pd

from indi_analyst.indicators import technical
from tests.conftest import make_ohlcv


def test_rsi_bounds_and_monotonic_up():
    # A strictly rising series should have a very high RSI.
    close = pd.Series(np.linspace(100, 200, 100))
    r = technical.rsi(close).iloc[-1]
    assert 0 <= r <= 100
    assert r > 90


def test_rsi_all_down_is_low():
    close = pd.Series(np.linspace(200, 100, 100))
    r = technical.rsi(close).iloc[-1]
    assert r < 10


def test_atr_positive():
    df = make_ohlcv("flat")
    a = technical.atr(df["High"], df["Low"], df["Close"]).iloc[-1]
    assert a > 0


def test_bollinger_ordering():
    df = make_ohlcv("flat")
    upper, mid, lower = technical.bollinger(df["Close"])
    assert upper.iloc[-1] >= mid.iloc[-1] >= lower.iloc[-1]


def test_compute_full_signalset_uptrend():
    df = make_ohlcv("up", n=300)
    sig = technical.compute(df)
    assert sig.last_close > 0
    assert sig.rsi_14 is not None
    assert sig.sma_200 is not None
    assert sig.atr_14 is not None and sig.atr_14 > 0
    assert sig.above_200sma is True  # strong uptrend closes above its 200-SMA
    assert sig.week52_high >= sig.last_close >= sig.week52_low
    assert 0.0 <= sig.week52_position <= 1.0


def test_supports_below_resistances_above():
    df = make_ohlcv("flat", n=300)
    sig = technical.compute(df)
    for s in sig.supports:
        assert s < sig.last_close
    for r in sig.resistances:
        assert r > sig.last_close


def test_short_history_preserves_unavailable_long_term_context():
    sig = technical.compute(make_ohlcv("up", n=60))

    assert sig.sma_200 is None
    assert sig.above_200sma is None
    assert sig.golden_cross is None
    assert sig.trend == "sideways"
