"""Construct the supported free price source."""

from __future__ import annotations

from indi_analyst.config import Settings, get_settings
from indi_analyst.datasources.base import PriceSource
from indi_analyst.datasources.throttle import RateLimiter
from indi_analyst.datasources.yfinance_source import YFinanceSource


def build_price_source(settings: Settings | None = None) -> PriceSource:
    """Construct the yfinance-backed price source, configured for rate/retry discipline.

    The returned source owns one `RateLimiter`; share a single source across scan threads so
    they're paced as a group (see `scan_universe`).
    """
    settings = settings or get_settings()
    limiter = RateLimiter(min_interval=settings.yf_min_request_interval)
    return YFinanceSource(
        limiter=limiter,
        retries=settings.yf_max_retries,
        backoff=settings.yf_retry_backoff,
    )
