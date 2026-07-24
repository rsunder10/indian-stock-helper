"""NSE-direct real-time price source.

yfinance prices lag the live NSE tape (often 15+ min, sometimes a day stale intraday). NSE's
quote API returns a *live single quote* (last price, day OHLC, prev close, volume) — but not a
year of bars. So this source keeps fetching the OHLCV **history from yfinance** and **overlays the
live NSE quote onto the latest bar**, so `last_close`, `change_pct`, and the latest-bar indicators
reflect a near-live price. Any NSE failure (403, offline, non-India IP, market closed) degrades
transparently to plain yfinance — a fetch here never raises.

Drops in behind the `PriceSource` protocol (datasources/base.py); the engine is untouched.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from indi_analyst.config import Settings, get_settings
from indi_analyst.datasources.yfinance_source import YFinanceSource
from indi_analyst.models import Fundamentals

_IST = ZoneInfo("Asia/Kolkata")

# NSE 403s bare HTTP clients; a browser-ish header set + a primed session cookie gets the JSON
# quote API through. (Mirrors screener/universe.py's _NSE_HEADERS, tuned for the JSON endpoint.)
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/equity",
}

# What _fetch_quote returns (or None): a small, source-agnostic quote dict.
Quote = dict  # keys: last_price, open, day_high, day_low, prev_close, volume (floats or None)
QuoteFetcher = Callable[[str], "Quote | None"]


def _f(value) -> float | None:
    """Coerce NSE's mixed-type JSON values (may carry commas) to float or None."""
    try:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.replace(",", "").strip()
            if not value or value == "-":
                return None
        v = float(value)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


class NSERealtimeSource:
    """A `PriceSource` that overlays a live NSE quote onto yfinance history.

    `fallback` supplies history/fundamentals/resolve (defaults to yfinance). `quote_fetcher` is
    injectable so tests can drive the overlay logic without any network.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fallback=None,
        quote_fetcher: QuoteFetcher | None = None,
    ):
        self._settings = settings or get_settings()
        self._fallback = fallback or YFinanceSource()
        self._quote_fetcher = quote_fetcher or self._http_fetch_quote
        # Shared, lazily-primed httpx client + a cap on simultaneous NSE hits (screener fan-out).
        self._client = None
        self._primed = False
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(max(1, self._settings.nse_quote_max_concurrency))

    # --- PriceSource protocol: resolve/fundamentals delegate to the fallback ---

    def resolve(self, query: str) -> tuple[str, str | None, str | None]:
        return self._fallback.resolve(query)

    def fundamentals(self, symbol: str) -> Fundamentals:
        return self._fallback.fundamentals(symbol)

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        df = self._fallback.history(symbol, period)
        # NSE only lists .NS symbols; leave BSE (.BO) and anything odd on plain yfinance.
        if not symbol.upper().endswith(".NS"):
            return df
        quote = self._quote_fetcher(symbol)
        if not quote or _f(quote.get("last_price")) is None:
            return df
        return self._overlay(df, quote)

    # --- Overlay: patch/append the latest bar with the live quote ---

    @staticmethod
    def _overlay(df: pd.DataFrame, quote: Quote) -> pd.DataFrame:
        """Return `df` with its latest bar reflecting the live quote.

        If the last bar is already today (IST) we update it in place; otherwise we append a fresh
        bar dated today. Ordering is preserved so downstream `.iloc[-1]`/rolling logic is correct.
        """
        if df is None or df.empty:
            return df

        last = _f(quote.get("last_price"))
        if last is None:
            return df
        day_high = _f(quote.get("day_high"))
        day_low = _f(quote.get("day_low"))
        day_open = _f(quote.get("open"))
        volume = _f(quote.get("volume"))

        df = df.copy()
        # yfinance's most-recent bar is sometimes unfinalized with a NaN Close. Drop such trailing
        # bars so the live quote replaces them cleanly (and change_pct is measured off a real bar).
        while not df.empty and _f(df["Close"].iloc[-1]) is None:
            df = df.iloc[:-1]
        if df.empty:
            return df

        last_ts = df.index[-1]
        today = datetime.now(_IST).date()
        last_date = last_ts.date() if hasattr(last_ts, "date") else pd.Timestamp(last_ts).date()

        if last_date == today:
            # Update today's bar: close = live; high/low widened to include the live extremes.
            prev_high = _f(df.at[last_ts, "High"])
            prev_low = _f(df.at[last_ts, "Low"])
            df.at[last_ts, "Close"] = last
            df.at[last_ts, "High"] = max(x for x in (prev_high, day_high, last) if x is not None)
            df.at[last_ts, "Low"] = min(x for x in (prev_low, day_low, last) if x is not None)
            if volume is not None and "Volume" in df.columns:
                df.at[last_ts, "Volume"] = volume
            return df

        # Append a new bar for today, matching the index's tz (naive or aware).
        tz = getattr(df.index, "tz", None)
        new_ts = pd.Timestamp(today)
        if tz is not None:
            new_ts = new_ts.tz_localize(tz)
        row = {
            "Open": day_open if day_open is not None else last,
            "High": max(x for x in (day_high, last) if x is not None),
            "Low": min(x for x in (day_low, last) if x is not None),
            "Close": last,
        }
        if "Volume" in df.columns:
            row["Volume"] = volume
        new_row = pd.DataFrame([{c: row.get(c) for c in df.columns}], index=[new_ts])
        return pd.concat([df, new_row])

    # --- Live NSE quote fetch (the default quote_fetcher) ---

    def _ensure_client(self):
        """Lazily create + cookie-prime a shared httpx client. Returns the client or None."""
        if self._primed and self._client is not None:
            return self._client
        with self._lock:
            if self._primed and self._client is not None:
                return self._client
            try:
                import httpx

                client = httpx.Client(
                    headers=_NSE_HEADERS,
                    timeout=self._settings.nse_quote_timeout,
                    follow_redirects=True,
                )
                # Priming GET sets the session cookie NSE's API requires.
                client.get(f"{self._settings.nse_quote_base_url}/get-quotes/equity")
                self._client = client
                self._primed = True
                return self._client
            except Exception:
                return None

    def _http_fetch_quote(self, symbol: str) -> Quote | None:
        """Fetch a live NSE quote. Returns None on any failure (never raises)."""
        nse_symbol = symbol.upper().removesuffix(".NS")
        with self._sem:
            try:
                client = self._ensure_client()
                if client is None:
                    return None
                url = f"{self._settings.nse_quote_base_url}/api/quote-equity"
                resp = client.get(url, params={"symbol": nse_symbol})
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                return None

        price = data.get("priceInfo") or {}
        intraday = price.get("intraDayHighLow") or {}
        security = (data.get("securityWiseDP") or {}) if isinstance(data, dict) else {}
        return {
            "last_price": price.get("lastPrice"),
            "open": price.get("open"),
            "day_high": intraday.get("max"),
            "day_low": intraday.get("min"),
            "prev_close": price.get("previousClose"),
            "volume": security.get("quantityTraded"),
        }
