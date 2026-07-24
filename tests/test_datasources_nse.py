"""Network-free tests for the NSE-direct real-time price source.

The whole point of `NSERealtimeSource` is that it fetches history from a fallback (yfinance) and
overlays a live NSE quote. We inject a fake `quote_fetcher` and a `MockPriceSource` fallback so no
network is ever touched — only the overlay/append/fallback/delegation logic is exercised.
"""

from __future__ import annotations

import pandas as pd
import pytest

from indi_analyst.config import Settings
from indi_analyst.datasources.factory import build_price_source
from indi_analyst.datasources.nse_source import NSERealtimeSource
from indi_analyst.datasources.yfinance_source import YFinanceSource
from indi_analyst.models import Fundamentals
from tests.conftest import MockPriceSource, make_ohlcv


def _df_ending(date, n: int = 60) -> pd.DataFrame:
    """A synthetic OHLCV frame whose last bar is dated exactly `date` (calendar days)."""
    df = make_ohlcv("up", n=n)
    df.index = pd.date_range(end=pd.Timestamp(date), periods=n, freq="D")
    return df


def _quote(**kw):
    base = {"last_price": None, "open": None, "day_high": None,
            "day_low": None, "prev_close": None, "volume": None}
    base.update(kw)
    return base


def _source(df, quote):
    fallback = MockPriceSource(df, symbol="RELIANCE.NS", name="Reliance")
    return NSERealtimeSource(Settings(), fallback=fallback, quote_fetcher=lambda s: quote)


def test_overlay_updates_todays_bar():
    today = pd.Timestamp.now(tz="Asia/Kolkata").normalize().tz_localize(None)
    df = _df_ending(today)
    src = _source(df, _quote(last_price=999.0, day_high=1010.0, day_low=980.0, volume=123.0))

    out = src.history("RELIANCE.NS")

    assert len(out) == len(df)  # updated in place, not appended
    assert out["Close"].iloc[-1] == 999.0
    assert out["High"].iloc[-1] >= 1010.0
    assert out["Low"].iloc[-1] <= 980.0
    assert out["Volume"].iloc[-1] == 123.0


def test_overlay_appends_when_last_bar_is_stale():
    df = _df_ending(pd.Timestamp("2024-06-03"))  # clearly not today
    src = _source(df, _quote(last_price=555.0, open=550.0, day_high=560.0, day_low=545.0, volume=7.0))

    out = src.history("RELIANCE.NS")

    assert len(out) == len(df) + 1  # a fresh bar was appended
    assert out["Close"].iloc[-1] == 555.0
    assert out["Open"].iloc[-1] == 550.0
    assert out.index[-1].date() == pd.Timestamp.now(tz="Asia/Kolkata").date()


def test_overlay_drops_trailing_nan_close_bar():
    # yfinance sometimes leaves the most-recent bar with a NaN Close; the overlay must strip it so
    # the live quote lands on a clean tail and change_pct is measured off a real bar.
    df = _df_ending(pd.Timestamp("2024-06-03"))
    import numpy as np
    df.loc[df.index[-1], "Close"] = np.nan  # break the last bar
    real_prev_close = df["Close"].iloc[-2]
    src = _source(df, _quote(last_price=555.0))

    out = src.history("RELIANCE.NS")

    assert out["Close"].iloc[-1] == 555.0
    assert out["Close"].iloc[-2] == real_prev_close  # the NaN bar was removed, not left behind
    assert not out["Close"].isna().any()


def test_none_quote_falls_back_to_unpatched_history():
    df = _df_ending(pd.Timestamp("2024-06-03"))
    src = _source(df, None)

    out = src.history("RELIANCE.NS")

    pd.testing.assert_frame_equal(out, df)


def test_missing_last_price_falls_back():
    df = _df_ending(pd.Timestamp("2024-06-03"))
    src = _source(df, _quote(day_high=10.0))  # no last_price

    out = src.history("RELIANCE.NS")

    pd.testing.assert_frame_equal(out, df)


def test_bse_symbol_is_left_on_fallback():
    df = _df_ending(pd.Timestamp("2024-06-03"))
    # A quote_fetcher that would blow up if called proves .BO never reaches it.
    def _boom(_):
        raise AssertionError("quote_fetcher must not be called for .BO symbols")

    fallback = MockPriceSource(df, symbol="TATASTEEL.BO")
    src = NSERealtimeSource(Settings(), fallback=fallback, quote_fetcher=_boom)

    out = src.history("TATASTEEL.BO")
    pd.testing.assert_frame_equal(out, df)


def test_resolve_and_fundamentals_delegate_to_fallback():
    df = _df_ending(pd.Timestamp("2024-06-03"))
    fund = Fundamentals(pe_ratio=15.5, sector="Energy")
    fallback = MockPriceSource(df, fundamentals=fund, symbol="RELIANCE.NS", name="Reliance")
    src = NSERealtimeSource(Settings(), fallback=fallback, quote_fetcher=lambda s: None)

    assert src.resolve("reliance") == ("RELIANCE.NS", "Reliance", "NSE")
    assert src.fundamentals("RELIANCE.NS") == fund


def test_factory_selects_sources():
    assert isinstance(build_price_source("yfinance"), YFinanceSource)
    assert isinstance(build_price_source("yf"), YFinanceSource)
    assert isinstance(build_price_source("nse", Settings()), NSERealtimeSource)
    with pytest.raises(ValueError):
        build_price_source("does-not-exist")


def test_factory_honors_config_default():
    assert isinstance(build_price_source(settings=Settings(default_price_source="nse")),
                      NSERealtimeSource)
    assert isinstance(build_price_source(settings=Settings(default_price_source="yfinance")),
                      YFinanceSource)
