"""Screener tests — fully network-free (mock price source, injected universe fetcher)."""

from __future__ import annotations

import pandas as pd
import pytest

from indi_analyst.config import get_settings
from indi_analyst.models import Action, Conviction, Fundamentals, StockSnapshot, TechnicalSignals
from indi_analyst.screener import scan_universe
from indi_analyst.screener.cache import ScanCache
from indi_analyst.screener.filters import apply, rank, resolve_preset
from indi_analyst.screener.models import Constituent, ScanResult, ScreenFilter, ScreenRow
from indi_analyst.screener.universe import load_universe
from tests.conftest import make_ohlcv


class MockMultiSource:
    """A price source that returns a distinct series per symbol — no network."""

    def __init__(self, spec: dict[str, str]):
        # spec maps symbol -> trend ("up"/"down"/"flat"); "bad" -> raises in history()
        self._spec = spec

    def resolve(self, query: str):
        return query, query.split(".")[0].title(), "NSE"

    def history(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        trend = self._spec.get(symbol, "flat")
        if trend == "bad":
            raise ValueError(f"No price history for {symbol}")
        return make_ohlcv(trend)

    def fundamentals(self, symbol: str) -> Fundamentals:
        return Fundamentals(
            pe_ratio=22.0, roe=0.19, debt_to_equity=0.4, revenue_growth=0.18,
            profit_margin=0.16, sector="Testing",
        )


def _fake_fetcher(members: list[Constituent]):
    def _fetch(name: str, base_url: str):
        return list(members)
    return _fetch


def _settings(tmp_path):
    s = get_settings()
    s.screener_cache_path = str(tmp_path / "scan.db")
    return s


# --- Batch scan -------------------------------------------------------------

def test_scan_universe_ranks_and_isolates_errors(tmp_path):
    settings = _settings(tmp_path)
    members = [
        Constituent(symbol="UP.NS", name="Up Corp", sector="Testing"),
        Constituent(symbol="DOWN.NS", name="Down Corp", sector="Testing"),
        Constituent(symbol="BAD.NS", name="Bad Corp", sector="Testing"),
    ]
    source = MockMultiSource({"UP.NS": "up", "DOWN.NS": "down", "BAD.NS": "bad"})

    result = scan_universe(
        "watchlist:UP.NS,DOWN.NS,BAD.NS",
        provider="rulebased",
        settings=settings,
        price_source=source,
        news_source=None,
        max_workers=2,
    )

    assert isinstance(result, ScanResult)
    assert len(result.rows) == 3
    assert result.error_count == 1  # BAD.NS isolated, scan still completed
    assert result.ok_count == 2

    ok = result.ok_rows()
    # Ranked by score descending; the uptrend should outscore the downtrend.
    up = next(r for r in ok if r.symbol == "UP.NS")
    down = next(r for r in ok if r.symbol == "DOWN.NS")
    assert up.score >= down.score
    assert result.rows[0].score is not None  # errored row sank to the bottom
    assert result.rows[-1].error is not None


def test_scan_uses_snapshot_cache(tmp_path):
    settings = _settings(tmp_path)
    settings.snapshot_cache_ttl_hours = 24
    cache = ScanCache(settings.screener_cache_path)

    class CountingSource(MockMultiSource):
        def __init__(self, spec):
            super().__init__(spec)
            self.history_calls = 0

        def history(self, symbol, period="1y"):
            self.history_calls += 1
            return super().history(symbol, period)

    source = CountingSource({"UP.NS": "up"})
    kwargs = dict(provider="rulebased", settings=settings, price_source=source,
                  news_source=None, cache=cache, max_workers=1)

    scan_universe("watchlist:UP.NS", **kwargs)
    first = source.history_calls
    scan_universe("watchlist:UP.NS", **kwargs)
    # Second scan should hit the snapshot cache, not re-fetch history.
    assert source.history_calls == first


# --- Universe loading + fallback -------------------------------------------

def test_load_universe_uses_live_fetch(tmp_path):
    settings = _settings(tmp_path)
    cache = ScanCache(settings.screener_cache_path)
    members = [Constituent(symbol="RELIANCE.NS", name="Reliance", sector="Energy")]
    got = load_universe("nifty50", settings=settings, cache=cache,
                        fetcher=_fake_fetcher(members))
    assert got[0].symbol == "RELIANCE.NS"


def test_load_universe_falls_back_to_bundled_on_fetch_failure(tmp_path):
    settings = _settings(tmp_path)
    cache = ScanCache(settings.screener_cache_path)

    def failing_fetch(name, base_url):
        raise RuntimeError("simulated NSE 403")

    warnings: list[str] = []
    got = load_universe("nifty50", settings=settings, cache=cache,
                        fetcher=failing_fetch, warnings=warnings)
    # Bundled NIFTY 50 fallback kicks in.
    assert any(m.symbol == "RELIANCE.NS" for m in got)
    assert len(got) >= 40
    assert warnings  # a degradation note was recorded


def test_load_universe_watchlist_and_unknown():
    got = load_universe("watchlist:tcs, infy.ns")
    assert [c.symbol for c in got] == ["TCS.NS", "INFY.NS"]
    with pytest.raises(ValueError):
        load_universe("nifty9000")


# --- Filters + presets ------------------------------------------------------

def _row(symbol, action, conv, score, rr=2.5, sector="Testing", pe=20.0):
    return ScreenRow(symbol=symbol, action=action, conviction=conv, score=score,
                     risk_reward=rr, sector=sector, pe_ratio=pe)


def test_filter_matches_and_rank():
    rows = [
        _row("A.NS", Action.BUY, Conviction.HIGH, 72),
        _row("B.NS", Action.HOLD, Conviction.LOW, 48),
        _row("C.NS", Action.ACCUMULATE, Conviction.MEDIUM, 60),
        ScreenRow(symbol="ERR.NS", error="boom"),
    ]
    flt = ScreenFilter(actions={Action.BUY, Action.ACCUMULATE}, min_score=55)
    kept = apply(rows, flt)
    assert {r.symbol for r in kept} == {"A.NS", "C.NS"}  # errored + HOLD dropped

    ranked = rank(kept, by="score")
    assert [r.symbol for r in ranked] == ["A.NS", "C.NS"]


def test_preset_resolves_and_narrows():
    preset = resolve_preset("high-conviction-buys")
    assert preset.min_conviction == Conviction.HIGH
    rows = [
        _row("HI.NS", Action.BUY, Conviction.HIGH, 70),
        _row("LO.NS", Action.BUY, Conviction.LOW, 70),
    ]
    kept = apply(rows, preset)
    assert [r.symbol for r in kept] == ["HI.NS"]


# --- Cache round-trip + temporal diff --------------------------------------

def test_snapshot_cache_roundtrip_and_ttl(tmp_path):
    cache = ScanCache(str(tmp_path / "c.db"))
    snap = StockSnapshot(
        symbol="X.NS", query="X", technicals=TechnicalSignals(last_close=100.0),
    )
    cache.put_snapshot(snap)
    assert cache.get_snapshot("X.NS", ttl_hours=24) is not None
    # A zero-length TTL makes any cached snapshot immediately stale.
    assert cache.get_snapshot("X.NS", ttl_hours=0) is None


def test_diff_scans_reports_action_changes(tmp_path):
    cache = ScanCache(str(tmp_path / "d.db"))
    r1 = ScanResult(universe="nifty50", provider="rulebased",
                    rows=[_row("A.NS", Action.HOLD, Conviction.LOW, 50)])
    cache.save_scan(r1)
    r2 = ScanResult(universe="nifty50", provider="rulebased",
                    rows=[_row("A.NS", Action.BUY, Conviction.HIGH, 70)])
    cache.save_scan(r2)

    diffs = cache.diff_scans("nifty50")
    a = next(d for d in diffs if d["symbol"] == "A.NS")
    assert a["old_action"] == "HOLD"
    assert a["new_action"] == "BUY"
    assert a["score_delta"] == 20.0
    assert a["changed"] is True
