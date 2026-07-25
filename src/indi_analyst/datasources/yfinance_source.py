"""yfinance-backed price + fundamentals source for NSE (.NS) / BSE (.BO) equities."""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from indi_analyst.models import Fundamentals


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
    """Resolves Indian tickers and fetches OHLCV + fundamentals from Yahoo Finance."""

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
        df = yf.Ticker(symbol).history(period=period, auto_adjust=False)
        if df is None or df.empty:
            raise ValueError(
                f"No price history for '{symbol}'. Check the ticker (try adding .NS or .BO)."
            )
        # Normalize column names / drop invalid closing bars for clean downstream handling.
        df = df.rename(columns=str.title)
        keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
        df = df[keep].dropna(how="all").dropna(subset=["Close"])
        if df.empty:
            raise ValueError(f"No valid closing prices for '{symbol}'.")
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

    @staticmethod
    def _safe_info(symbol: str) -> dict:
        try:
            info = yf.Ticker(symbol).info
            return info if isinstance(info, dict) else {}
        except Exception:
            return {}
