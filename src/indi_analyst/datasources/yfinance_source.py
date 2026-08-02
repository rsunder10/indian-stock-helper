"""yfinance-backed price + fundamentals source for NSE (.NS) / BSE (.BO) equities.

The free Yahoo endpoint has no SLA — it rate-limits bursts, blips intermittently, and
occasionally returns malformed bars (a zero price, an out-of-order date, a High below the Low).
So every network call is paced by a shared `RateLimiter` and wrapped in `retry`, and `history()`
validates the OHLCV frame at the boundary, dropping bad bars and recording what it did in
`df.attrs["warnings"]` (plus `source` / `as_of` provenance) rather than letting garbage flow
downstream into a recommendation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import yfinance as yf

from indi_analyst.datasources.throttle import RateLimiter, retry
from indi_analyst.models import CorporateActions, Fundamentals

_OHLC = ["Open", "High", "Low", "Close"]


def _split_ratio(v: float) -> str | None:
    """Format a yfinance split factor as a human ratio. 2.0 -> "2:1"; 0.2 -> "1:5" (reverse)."""
    if not v or v <= 0 or v == 1:
        return None
    return f"{v:g}:1" if v > 1 else f"1:{1 / v:g}"


def _corporate_actions(
    actions,
    *,
    as_of: date | None,
    lookback_years: int,
    split_recency_days: int,
) -> CorporateActions | None:
    """Parse yfinance's ``Ticker.actions`` frame into a `CorporateActions`, or None if empty.

    Pure/offline so it can be unit-tested with a synthetic frame. Returns None only when there
    is no action history at all; a stock with history but no recent dividends yields a real
    zero-count (which the valuation gate treats as "not a consistent payer"), not None.
    """
    if not isinstance(actions, pd.DataFrame) or actions.empty:
        return None
    try:
        dates = [pd.Timestamp(ts).date() for ts in actions.index]
    except Exception:
        return None

    ref = as_of or max(dates)
    div_col = actions["Dividends"].to_list() if "Dividends" in actions.columns else None
    split_col = actions["Stock Splits"].to_list() if "Stock Splits" in actions.columns else None

    dividend_paying_years = last_dividend = last_dividend_date = None
    if div_col is not None:
        min_year = ref.year - lookback_years + 1
        paid_years: set[int] = set()
        for d, v in zip(dates, div_col, strict=False):
            if d > ref:
                continue
            if v and v > 0:
                if min_year <= d.year <= ref.year:
                    paid_years.add(d.year)
                if last_dividend_date is None or d > last_dividend_date:
                    last_dividend_date, last_dividend = d, float(v)
        dividend_paying_years = len(paid_years)

    last_split_date = last_split_ratio = recent_split = None
    if split_col is not None:
        for d, v in zip(dates, split_col, strict=False):
            if (
                d <= ref
                and v
                and v > 0
                and v != 1
                and (last_split_date is None or d > last_split_date)
            ):
                last_split_date, last_split_ratio = d, _split_ratio(float(v))
        if last_split_date is not None:
            age_days = (ref - last_split_date).days
            recent_split = 0 <= age_days <= split_recency_days

    return CorporateActions(
        dividend_paying_years=dividend_paying_years,
        lookback_years=lookback_years if div_col is not None else None,
        last_dividend=last_dividend,
        last_dividend_date=last_dividend_date,
        last_split_ratio=last_split_ratio,
        last_split_date=last_split_date,
        recent_split=recent_split,
    )


def _earnings_date(calendar) -> datetime | None:
    """Extract the next earnings date from yfinance's calendar (dict or DataFrame), else None.

    yfinance returns either a ``dict`` (``{"Earnings Date": [date, ...], ...}``) on newer
    versions or a DataFrame on older ones. We read the first future-facing value and coerce it
    to a tz-aware UTC datetime, tolerating whatever shape shows up.
    """
    value = None
    try:
        if isinstance(calendar, dict):
            value = calendar.get("Earnings Date")
        elif isinstance(calendar, pd.DataFrame) and "Earnings Date" in calendar.index:
            value = calendar.loc["Earnings Date"].to_list()
    except Exception:
        return None

    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None

    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, date):
            dt = datetime(value.year, value.month, value.day)
        else:
            dt = pd.Timestamp(value).to_pydatetime()
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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
            if info and (
                info.get("regularMarketPrice")
                or info.get("currentPrice")
                or info.get("previousClose")
            ):
                exchange = "NSE" if sym.endswith(".NS") else "BSE"
                name = info.get("longName") or info.get("shortName")
                return sym, name, exchange

        # Nothing validated — return the first candidate and let history() surface the error.
        first = candidates[0]
        return first, None, ("NSE" if first.endswith(".NS") else "BSE")

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        df = self._history_raw(symbol, period)
        if not isinstance(df, pd.DataFrame) or df.empty:
            raise ValueError(
                f"No price history for '{symbol}'. Check the ticker (try adding .NS or .BO)."
            )
        # Normalize column names, keep only OHLCV.
        df = df.rename(columns=str.title)
        missing = [c for c in _OHLC if c not in df.columns]
        if missing:
            raise ValueError(
                f"Price history for '{symbol}' is missing required column(s): {', '.join(missing)}."
            )
        keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
        df = df[keep].dropna(how="all").copy()

        warnings: list[str] = []

        # Provider payloads occasionally contain numeric-looking strings or malformed dates.
        # Coerce at the boundary so the indicator layer receives real numeric/datetime values.
        for col in keep:
            raw = df[col]
            converted = pd.to_numeric(raw, errors="coerce")
            invalid = raw.notna() & converted.isna()
            if invalid.any():
                warnings.append(
                    f"Coerced {int(invalid.sum())} non-numeric {col} value(s) to missing."
                )
            df[col] = converted

        try:
            normalized_index = pd.to_datetime(df.index, errors="coerce")
            invalid_index = pd.isna(normalized_index)
            if invalid_index.any():
                warnings.append(f"Dropped {int(invalid_index.sum())} bar(s) with invalid dates.")
                df = df.loc[~invalid_index].copy()
                normalized_index = normalized_index[~invalid_index]
            df.index = normalized_index
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Price history for '{symbol}' has invalid dates: {exc}") from exc

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
                warnings.append(
                    f"Dropped {dropped} bar(s) with inconsistent OHLC (High/Low out of range)."
                )

        if "Volume" in df.columns:
            negative_volume = df["Volume"] < 0
            if negative_volume.any():
                df.loc[negative_volume, "Volume"] = float("nan")
                warnings.append(
                    f"Replaced {int(negative_volume.sum())} negative volume value(s) with missing."
                )

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
            price_to_sales=_f(info.get("priceToSalesTrailing12Months")),
            roe=_f(info.get("returnOnEquity")),
            debt_to_equity=_f(info.get("debtToEquity")),
            profit_margin=_f(info.get("profitMargins")),
            revenue_growth=_f(info.get("revenueGrowth")),
            earnings_growth=_f(info.get("earningsGrowth")),
            dividend_yield=_f(info.get("dividendYield")),
            beta=_f(info.get("beta")),
            eps=_f(info.get("trailingEps")),
            book_value=_f(info.get("bookValue")),
            dividend_rate=_f(info.get("dividendRate")),
            revenue_per_share=_f(info.get("revenuePerShare")),
            next_earnings_date=self._next_earnings_date(symbol),
            sector=info.get("sector"),
            industry=info.get("industry"),
        )

    def corporate_actions(
        self,
        symbol: str,
        *,
        as_of: date | None = None,
        lookback_years: int = 6,
        split_recency_days: int = 365,
    ) -> CorporateActions | None:
        """Best-effort dividend + split history from Yahoo's single ``actions`` frame.

        One rate-limited/retried call. Degrades to None on any failure or absence, so a stock
        with no corporate-action history leaves the snapshot's `corporate_actions` unset rather
        than raising.
        """
        self._limiter.acquire()
        try:
            actions = retry(
                lambda: yf.Ticker(symbol).actions,
                retries=self._retries,
                backoff=self._backoff,
            )
        except Exception:
            return None
        return _corporate_actions(
            actions,
            as_of=as_of,
            lookback_years=lookback_years,
            split_recency_days=split_recency_days,
        )

    def _next_earnings_date(self, symbol: str):
        """Best-effort next results date from Yahoo's calendar. None on any failure/absence."""
        self._limiter.acquire()
        try:
            calendar = retry(
                lambda: yf.Ticker(symbol).calendar,
                retries=self._retries,
                backoff=self._backoff,
            )
        except Exception:
            return None
        return _earnings_date(calendar)

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
