"""Select and construct a price source from config. Mirrors llm/factory.py.

The default (`yfinance`) is byte-for-byte the pre-existing behavior; `nse` layers a live NSE
quote on top of yfinance history and falls back to yfinance on any NSE failure.
"""

from __future__ import annotations

from indi_analyst.config import Settings, get_settings
from indi_analyst.datasources.base import PriceSource
from indi_analyst.datasources.yfinance_source import YFinanceSource


def build_price_source(name: str | None = None, settings: Settings | None = None) -> PriceSource:
    """Construct the named price source (defaults to config's `default_price_source`)."""
    settings = settings or get_settings()
    name = (name or settings.default_price_source).lower()

    if name in ("yfinance", "yf"):
        return YFinanceSource()
    if name == "nse":
        from indi_analyst.datasources.nse_source import NSERealtimeSource

        return NSERealtimeSource(settings)

    raise ValueError(f"Unknown price source '{name}'. Use 'yfinance' or 'nse'.")
