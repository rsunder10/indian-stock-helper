"""Concurrent universe scan — the heart of the screener.

Reuses the single-stock pipeline untouched: `build_snapshot` (cache-first) then
`analyze_snapshot` (levels + score + per-stock verdict). Each symbol is independent, so we
fan out over a thread pool (yfinance and LLM calls are I/O-bound) and isolate failures per
symbol — one bad ticker never sinks the scan.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from typing import Callable

from indi_analyst.analysis.engine import analyze_snapshot
from indi_analyst.analysis.snapshot import DEFAULT_NEWS_SOURCE, build_snapshot
from indi_analyst.config import Settings, get_settings
from indi_analyst.datasources.factory import build_price_source
from indi_analyst.models import Recommendation
from indi_analyst.screener.cache import ScanCache
from indi_analyst.screener.models import Constituent, ScanResult, ScreenRow
from indi_analyst.screener.universe import load_universe

ProgressCb = Callable[[int, int, str], None]


def _snapshot_cache_key(settings: Settings, price_source, news_source) -> str:
    """Hash the inputs that change a cached deterministic snapshot.

    The DB cache is intentionally short-lived, but a symbol alone is not enough: changing the
    history period, news policy, source adapter, or macro pack must not silently reuse old data.
    """
    source_name = f"{type(price_source).__module__}.{type(price_source).__qualname__}"
    if news_source is DEFAULT_NEWS_SOURCE:
        news_name = "default-google-news"
    elif news_source is None:
        news_name = "news-disabled"
    else:
        news_name = f"{type(news_source).__module__}.{type(news_source).__qualname__}"
    names = [
        "history_period", "news_max_items", "news_recency_halflife_days",
        "corporate_action_lookback_years", "dividend_min_consistent_years", "split_recency_days",
        "budget_enabled", "budget_year", "budget_data_path",
        "rate_enabled", "rate_pack_version", "rate_data_path",
        "iip_enabled", "iip_pack_version", "iip_data_path",
        "gst_enabled", "gst_pack_version", "gst_data_path",
        "credit_enabled", "credit_pack_version", "credit_data_path",
        "trade_enabled", "trade_pack_version", "trade_data_path",
        "inputcost_enabled", "inputcost_pack_version", "inputcost_data_path",
        "monsoon_enabled", "monsoon_pack_version", "monsoon_data_path",
    ]
    material = [source_name, news_name]
    material.extend(f"{name}={getattr(settings, name)}" for name in names)
    return sha256("\x1f".join(material).encode("utf-8")).hexdigest()[:20]


def _row_from_recommendation(c: Constituent, rec: Recommendation) -> ScreenRow:
    t = rec.snapshot.technicals
    lv = rec.levels
    bud = rec.snapshot.budget_signal
    return ScreenRow(
        symbol=rec.snapshot.symbol,
        name=rec.snapshot.name or c.name,
        sector=rec.snapshot.fundamentals.sector or c.sector,
        action=rec.action,
        conviction=rec.conviction,
        score=rec.quant.score,
        technical_score=rec.quant.technical_score,
        fundamental_score=rec.quant.fundamental_score,
        last_close=t.last_close,
        change_pct=t.change_pct,
        pe_ratio=rec.snapshot.fundamentals.pe_ratio,
        fair_value=rec.valuation.fair_value,
        margin_of_safety=rec.valuation.margin_of_safety,
        risk_reward=lv.risk_reward,
        entry_low=lv.entry_low,
        entry_high=lv.entry_high,
        stop_loss=lv.stop_loss,
        target_1=lv.target_1,
        trend=t.trend,
        thesis=list(rec.verdict.thesis[:4]),
        provider=rec.provider,
        macro_points=rec.quant.macro_adjustment,
        macro_signals=list(rec.snapshot.macro_signals),
        budget_tailwind=bud.tailwind if bud is not None else None,
        budget_drivers=list(bud.drivers) if bud is not None else [],
    )


def _scan_one(
    c: Constituent,
    *,
    provider: str | None,
    settings: Settings,
    cache: ScanCache | None,
    use_cache: bool,
    price_source,
    news_source,
    snapshot_cache_key: str,
) -> ScreenRow:
    """Scan a single constituent. Never raises — failures become an errored row."""
    try:
        snapshot = None
        if use_cache and cache is not None:
            snapshot = cache.get_snapshot(
                c.symbol,
                ttl_hours=settings.snapshot_cache_ttl_hours,
                cache_key=snapshot_cache_key,
            )
        if snapshot is None:
            snapshot = build_snapshot(
                c.symbol,
                settings=settings,
                price_source=price_source,
                news_source=news_source,
            )
            if use_cache and cache is not None:
                cache.put_snapshot(snapshot, cache_key=snapshot_cache_key)

        rec = analyze_snapshot(snapshot, provider=provider, settings=settings)
        return _row_from_recommendation(c, rec)
    except Exception as e:  # bad ticker, no history, provider blow-up — isolate it
        return ScreenRow(symbol=c.symbol, name=c.name, sector=c.sector, error=str(e))


def scan_universe(
    universe: str,
    *,
    provider: str | None = None,
    settings: Settings | None = None,
    price_source=None,
    news_source=DEFAULT_NEWS_SOURCE,
    limit: int | None = None,
    use_cache: bool = True,
    persist: bool = True,
    max_workers: int | None = None,
    on_progress: ProgressCb | None = None,
    cache: ScanCache | None = None,
) -> ScanResult:
    """Scan every constituent of `universe` and return a score-ranked `ScanResult`.

    `provider` selects the LLM (defaults to config); rule-based keeps scans fast/free.
    Sources are injectable (tests pass a network-free mock). `limit` caps the symbol count.
    """
    settings = settings or get_settings()
    resolved_provider = (provider or settings.default_llm_provider)

    # Build one shared, rate-limited source so all worker threads are paced as a group
    # (a single RateLimiter instance) instead of each thread constructing its own unthrottled one.
    if price_source is None:
        price_source = build_price_source(settings=settings)
    snapshot_cache_key = _snapshot_cache_key(settings, price_source, news_source)

    if cache is None and (use_cache or persist):
        cache = ScanCache(settings.screener_cache_path)

    warnings: list[str] = []
    members = load_universe(
        universe, settings=settings, cache=cache, warnings=warnings
    )
    if limit is not None:
        members = members[:limit]

    total = len(members)
    rows: list[ScreenRow] = []
    workers = max_workers or settings.screener_max_workers

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _scan_one,
                c,
                provider=provider,
                settings=settings,
                cache=cache,
                use_cache=use_cache,
                price_source=price_source,
                news_source=news_source,
                snapshot_cache_key=snapshot_cache_key,
            ): c
            for c in members
        }
        done = 0
        for fut in as_completed(futures):
            rows.append(fut.result())
            done += 1
            if on_progress is not None:
                on_progress(done, total, futures[fut].symbol)

    # Rank by score (errored rows sink to the bottom).
    from indi_analyst.screener.filters import rank

    rows = rank(rows, by="score")

    result = ScanResult(
        universe=universe,
        provider=resolved_provider,
        rows=rows,
        warnings=warnings,
    )
    if persist and cache is not None:
        try:
            cache.save_scan(result)
        except Exception as e:
            result.warnings.append(f"Could not persist scan history: {e}")
    return result
