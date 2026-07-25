"""yfinance-backed price + fundamentals source for NSE (.NS) / BSE (.BO) equities.

The free Yahoo endpoint has no SLA — it rate-limits bursts, blips intermittently, and
occasionally returns malformed bars (a zero price, an out-of-order date, a High below the Low).
So every network call is paced by a shared `RateLimiter` and wrapped in `retry`, and `history()`
validates the OHLCV frame at the boundary, dropping bad bars and recording what it did in
`df.attrs["warnings"]` (plus `source` / `as_of` provenance) rather than letting garbage flow
downstream into a recommendation.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from indi_analyst.datasources.throttle import RateLimiter, retry
from indi_analyst.models import Fundamentals

_OHLC = ["Open", "High", "Low", "Close"]


def _f(value) -> float | None:
    """Coerce yfinance's mixed-type .info values to float or None."""
    try:
        if value is None:
            return None
        v = float(value)
        # yfinance sometimes returns sentinel-ish infinities / NaNs
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v
    except (TypeError, ValueError):
        return None


class YFinanceSource:
    """Resolves Indian tickers and fetches OHLCV + fundamentals from Yahoo Finance.

    `limiter` paces network calls (share one instance across scan threads); `retries`/`backoff`
    govern transient-failure retries. Defaults keep direct construction (and tests) network-cheap.
    """

    def __init__(
        self,
        *,
        limiter: RateLimiter | None = None,
        retries: int = 3,
        backoff: float = 0.5,
    ) -> None:
        self._limiter = limiter or RateLimiter()
        self._retries = retries
        self._backoff = backoff

    def _history_raw(self, symbol: str, period: str) -> pd.DataFrame:
        self._limiter.acquire()
        return retry(
            lambda: yf.Ticker(symbol).history(period=period, auto_adjust=False),
            retries=self._retries,
            backoff=self._backoff,
        )

    def resolve(self, query: str) -> tuple[str, str | None, str | None]:
        """Return (symbol, display_name, exchange).

        Accepts a bare symbol (RELIANCE), an explicit Yahoo symbol (RELIANCE.NS),
        or lowercase. Prefers NSE (.NS); falls back to BSE (.BO).
        """
        q = query.strip().upper()
        if q.endswith(".NS") or q.endswith(".BO"):
            candidates = [q]
        else:
            candidates = [f"{q}.NS", f"{q}.BO"]

        for sym in candidates:
            info = self._safe_info(sym)
            # a valid ticker returns a price field
            if info and (info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")):
                exchange = "NSE" if sym.endswith(".NS") else "BSE"
                name = info.get("longName") or info.get("shortName")
                return sym, name, exchange

        # Nothing validated — return the first candidate and let history() surface the error.
        first = candidates[0]
        return first, None, ("NSE" if first.endswith(".NS") else "BSE")

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        df = self._history_raw(symbol, period)
        if df is None or df.empty:
            raise ValueError(
                f"No price history for '{symbol}'. Check the ticker (try adding .NS or .BO)."
            )
        # Normalize column names, keep only OHLCV.
        df = df.rename(columns=str.title)
        keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
        df = df[keep].dropna(how="all")

        warnings: list[str] = []

        # Chronological order — indicators assume ascending dates.
        if not df.index.is_monotonic_increasing:
            df = df.sort_index()
            warnings.append("Price history was out of order; re-sorted chronologically.")

        # Drop bars missing any OHLC value (was: only Close).
        before = len(df)
        df = df.dropna(subset=[c for c in _OHLC if c in df.columns])
        if (dropped := before - len(df)) > 0:
            warnings.append(f"Dropped {dropped} bar(s) with missing OHLC values.")

        ohlc = [c for c in _OHLC if c in df.columns]
        if ohlc and not df.empty:
            # Non-positive prices are impossible for a listed equity — drop them.
            before = len(df)
            df = df[(df[ohlc] > 0).all(axis=1)]
            if (dropped := before - len(df)) > 0:
                warnings.append(f"Dropped {dropped} bar(s) with non-positive prices.")

        if {"Open", "High", "Low", "Close"}.issubset(df.columns) and not df.empty:
            # OHLC must be internally consistent: High is the max, Low is the min of the bar.
            hi_ok = (df["High"] >= df["Low"]) & (df["High"] >= df[["Open", "Close"]].max(axis=1))
            lo_ok = df["Low"] <= df[["Open", "Close"]].min(axis=1)
            before = len(df)
            df = df[hi_ok & lo_ok]
            if (dropped := before - len(df)) > 0:
                warnings.append(f"Dropped {dropped} bar(s) with inconsistent OHLC (High/Low out of range).")

        if df.empty:
            raise ValueError(f"No valid price bars for '{symbol}' after data-quality checks.")

        df.attrs["source"] = "yfinance"
        df.attrs["as_of"] = df.index[-1].to_pydatetime()
        df.attrs["warnings"] = warnings
        return df

    def fundamentals(self, symbol: str) -> Fundamentals:
        info = self._safe_info(symbol)
        if not info:
            return Fundamentals()
        return Fundamentals(
            market_cap=_f(info.get("marketCap")),
            pe_ratio=_f(info.get("trailingPE")),
            forward_pe=_f(info.get("forwardPE")),
            pb_ratio=_f(info.get("priceToBook")),
            roe=_f(info.get("returnOnEquity")),
            debt_to_equity=_f(info.get("debtToEquity")),
            profit_margin=_f(info.get("profitMargins")),
            revenue_growth=_f(info.get("revenueGrowth")),
            earnings_growth=_f(info.get("earningsGrowth")),
            dividend_yield=_f(info.get("dividendYield")),
            beta=_f(info.get("beta")),
            sector=info.get("sector"),
            industry=info.get("industry"),
        )

    def _safe_info(self, symbol: str) -> dict:
        # Fundamentals are best-effort: retry a transient blip, then degrade to {} rather than raise.
        self._limiter.acquire()
        try:
            info = retry(
                lambda: yf.Ticker(symbol).info,
                retries=self._retries,
                backoff=self._backoff,
            )
            return info if isinstance(info, dict) else {}
        except Exception:
            return {}
