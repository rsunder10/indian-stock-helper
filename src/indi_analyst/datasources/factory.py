"""Construct the supported free price source."""

from __future__ import annotations

from indi_analyst.config import Settings
from indi_analyst.datasources.base import PriceSource
from indi_analyst.datasources.yfinance_source import YFinanceSource


def build_price_source(settings: Settings | None = None) -> PriceSource:
    """Construct the supported yfinance-backed price source."""
    return YFinanceSource()
