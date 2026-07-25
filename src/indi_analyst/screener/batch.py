"""Concurrent universe scan — the heart of the screener.

Reuses the single-stock pipeline untouched: `build_snapshot` (cache-first) then
`analyze_snapshot` (levels + score + per-stock verdict). Each symbol is independent, so we
fan out over a thread pool (yfinance and LLM calls are I/O-bound) and isolate failures per
symbol — one bad ticker never sinks the scan.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from indi_analyst.analysis.engine import analyze_snapshot
from indi_analyst.analysis.snapshot import build_snapshot
from indi_analyst.config import Settings, get_settings
from indi_analyst.models import Recommendation
from indi_analyst.screener.cache import ScanCache
from indi_analyst.screener.models import Constituent, ScanResult, ScreenRow
from indi_analyst.screener.universe import load_universe

ProgressCb = Callable[[int, int, str], None]


def _row_from_recommendation(c: Constituent, rec: Recommendation) -> ScreenRow:
    t = rec.snapshot.technicals
    lv = rec.levels
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
        risk_reward=lv.risk_reward,
        entry_low=lv.entry_low,
        entry_high=lv.entry_high,
        stop_loss=lv.stop_loss,
        target_1=lv.target_1,
        trend=t.trend,
        thesis=list(rec.verdict.thesis[:4]),
        provider=rec.provider,
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
) -> ScreenRow:
    """Scan a single constituent. Never raises — failures become an errored row."""
    try:
        snapshot = None
        if use_cache and cache is not None:
            snapshot = cache.get_snapshot(c.symbol, ttl_hours=settings.snapshot_cache_ttl_hours)
        if snapshot is None:
            snapshot = build_snapshot(
                c.symbol,
                settings=settings,
                price_source=price_source,
                news_source=news_source,
            )
            if use_cache and cache is not None:
                cache.put_snapshot(snapshot)

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
    news_source=None,
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
