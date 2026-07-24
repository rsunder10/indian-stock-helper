"""Technical indicators, computed directly with pandas/numpy.

Deliberately dependency-light: the core set (RSI, MACD, moving averages, Bollinger,
ADX, ATR, support/resistance) is small enough to implement cleanly and avoids the
numpy/pandas version breakage that plagues pandas-ta on Python 3.13.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indi_analyst.models import TechnicalSignals


def _last(series: pd.Series) -> float | None:
    if series is None or series.empty:
        return None
    val = series.iloc[-1]
    if pd.isna(val):
        return None
    return float(val)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    # No losses over the window -> RSI is 100 by definition (avoid NaN from divide-by-zero).
    # Only override where avg_loss is a real 0, not during the warmup NaNs.
    out = out.mask((avg_loss == 0) & avg_loss.notna(), 100.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(close: pd.Series, period: int = 20, k: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid + k * std, mid, mid - k * std


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean() / atr_

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def swing_levels(df: pd.DataFrame, price: float, window: int = 5, lookback: int = 180):
    """Recent swing highs/lows split into supports (below price) and resistances (above)."""
    recent = df.tail(lookback)
    highs, lows = recent["High"], recent["Low"]
    pivots_hi, pivots_lo = [], []
    for i in range(window, len(recent) - window):
        seg_hi = highs.iloc[i - window : i + window + 1]
        seg_lo = lows.iloc[i - window : i + window + 1]
        if highs.iloc[i] == seg_hi.max():
            pivots_hi.append(float(highs.iloc[i]))
        if lows.iloc[i] == seg_lo.min():
            pivots_lo.append(float(lows.iloc[i]))

    supports = sorted({round(p, 2) for p in pivots_lo + pivots_hi if p < price}, reverse=True)
    resistances = sorted({round(p, 2) for p in pivots_hi + pivots_lo if p > price})
    return supports[:3], resistances[:3]


def compute(df: pd.DataFrame) -> TechnicalSignals:
    """Compute the full indicator set from an OHLCV DataFrame."""
    close, high, low, vol = df["Close"], df["High"], df["Low"], df.get("Volume")

    last_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2]) if len(close) > 1 else None
    change_pct = ((last_close - prev_close) / prev_close) if prev_close else None

    macd_line, signal_line, hist = macd(close)
    bb_u, bb_m, bb_l = bollinger(close)
    atr_series = atr(high, low, close)
    adx_series = adx(high, low, close)

    sma_20 = _last(close.rolling(20).mean())
    sma_50 = _last(close.rolling(50).mean())
    sma_200 = _last(close.rolling(200).mean())
    ema_20 = _last(close.ewm(span=20, adjust=False).mean())

    win = close.tail(252)  # ~1 trading year
    week52_high = float(win.max())
    week52_low = float(win.min())
    week52_position = (
        (last_close - week52_low) / (week52_high - week52_low)
        if week52_high > week52_low
        else None
    )

    volume = _last(vol) if vol is not None else None
    avg_volume_20 = _last(vol.rolling(20).mean()) if vol is not None else None
    volume_ratio = (volume / avg_volume_20) if (volume and avg_volume_20) else None

    supports, resistances = swing_levels(df, last_close)

    golden_cross = (sma_50 is not None and sma_200 is not None) and sma_50 > sma_200
    above_200sma = (sma_200 is not None) and last_close > sma_200
    if sma_50 is not None and sma_200 is not None:
        if above_200sma and golden_cross:
            trend = "uptrend"
        elif not above_200sma and not golden_cross:
            trend = "downtrend"
        else:
            trend = "sideways"
    else:
        trend = "sideways" if above_200sma is None else ("uptrend" if above_200sma else "downtrend")

    return TechnicalSignals(
        last_close=last_close,
        prev_close=prev_close,
        change_pct=change_pct,
        rsi_14=_last(rsi(close)),
        macd=_last(macd_line),
        macd_signal=_last(signal_line),
        macd_hist=_last(hist),
        sma_20=sma_20,
        sma_50=sma_50,
        sma_200=sma_200,
        ema_20=ema_20,
        bb_upper=_last(bb_u),
        bb_lower=_last(bb_l),
        bb_mid=_last(bb_m),
        adx_14=_last(adx_series),
        atr_14=_last(atr_series),
        week52_high=week52_high,
        week52_low=week52_low,
        week52_position=week52_position,
        volume=volume,
        avg_volume_20=avg_volume_20,
        volume_ratio=volume_ratio,
        supports=supports,
        resistances=resistances,
        trend=trend,
        golden_cross=golden_cross if sma_200 is not None else None,
        above_200sma=above_200sma if sma_200 is not None else None,
    )
