"""Screener tests — fully network-free (mock price source, injected universe fetcher)."""

from __future__ import annotations

import pandas as pd
import pytest

from indi_analyst.config import get_settings
from indi_analyst.models import Action, Conviction, Fundamentals, StockSnapshot, TechnicalSignals
from indi_analyst.screener import scan_universe, summarize_sectors
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


def test_snapshot_cache_isolated_by_history_settings(tmp_path):
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
    scan_universe(
        "watchlist:UP.NS", provider="rulebased", settings=settings,
        price_source=source, news_source=None, cache=cache, max_workers=1,
    )
    first = source.history_calls

    changed = _settings(tmp_path)
    changed.snapshot_cache_ttl_hours = 24
    changed.history_period = "2y"
    scan_universe(
        "watchlist:UP.NS", provider="rulebased", settings=changed,
        price_source=source, news_source=None, cache=cache, max_workers=1,
    )

    assert first == 1
    assert source.history_calls == 2


def test_load_universe_uses_bundled_data_without_network(tmp_path):
    settings = _settings(tmp_path)
    cache = ScanCache(settings.screener_cache_path)
    got = load_universe("nifty50", settings=settings, cache=cache)

    assert len(got) >= 40
    assert any(m.symbol == "RELIANCE.NS" for m in got)


def test_load_universe_watchlist_and_unknown():
    got = load_universe("watchlist:tcs, infy.ns")
    assert [c.symbol for c in got] == ["TCS.NS", "INFY.NS"]
    with pytest.raises(ValueError):
        load_universe("nifty9000")


def test_load_universe_nifty200_uses_bundled_pack(tmp_path):
    """nifty200 resolves to its own ~200-name pack, not the NIFTY 50 fallback."""
    settings = _settings(tmp_path)
    cache = ScanCache(settings.screener_cache_path)
    warnings: list[str] = []
    got = load_universe("nifty200", settings=settings, cache=cache, warnings=warnings)

    assert len(got) >= 180
    assert any(m.symbol == "RELIANCE.NS" for m in got)
    # A real pack ships, so there must be no fallback-to-nifty50 warning.
    assert not any("falling back" in w for w in warnings)


def test_load_universe_nifty500_uses_bundled_pack(tmp_path):
    settings = _settings(tmp_path)
    cache = ScanCache(settings.screener_cache_path)
    got = load_universe("nifty500", settings=settings, cache=cache)
    assert len(got) >= 450


def test_load_universe_falls_back_when_pack_missing(monkeypatch, tmp_path):
    """If a recognized index has no bundled pack, degrade to nifty50 with a warning."""
    import indi_analyst.screener.universe as u

    real_bundled = u._bundled

    def _no_pack(key: str):
        # Simulate a missing pack for nifty500 only; keep nifty50 available.
        return None if key == "nifty500" else real_bundled(key)

    monkeypatch.setattr(u, "_bundled", _no_pack)
    monkeypatch.setattr(u, "INDEX_UNIVERSES", u.INDEX_UNIVERSES | {"nifty500"})

    settings = _settings(tmp_path)
    cache = ScanCache(settings.screener_cache_path)
    warnings: list[str] = []
    got = load_universe("nifty500", settings=settings, cache=cache, warnings=warnings)

    assert any(m.symbol == "RELIANCE.NS" for m in got)  # nifty50 contents
    assert any("falling back to the bundled NIFTY 50 list" in w for w in warnings)


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


# --- Sector summary (top-down budget view) ---------------------------------

def test_summarize_sectors_groups_and_ranks_by_tailwind():
    rows = [
        _row("A.NS", Action.BUY, Conviction.HIGH, 70, sector="Defence"),
        _row("B.NS", Action.HOLD, Conviction.LOW, 50, sector="Defence"),
        _row("C.NS", Action.ACCUMULATE, Conviction.MEDIUM, 60, sector="IT"),
        ScreenRow(symbol="ERR.NS", error="boom", sector="Defence"),  # errored -> ignored
    ]
    rows[0].budget_tailwind = 0.7  # Defence carries a strong tailwind
    rows[1].budget_tailwind = 0.7
    rows[0].budget_drivers = ["Defence outlay +9% YoY"]
    rows[2].budget_tailwind = 0.1  # IT weaker

    summaries = summarize_sectors(rows)
    assert [s.sector for s in summaries] == ["Defence", "IT"]  # ranked by tailwind

    defence = summaries[0]
    assert defence.n_stocks == 2  # errored row excluded
    assert defence.avg_score == 60.0  # mean(70, 50)
    assert defence.top_symbols[0] == "A.NS"  # highest-scoring first
    assert defence.budget_tailwind == 0.7
    assert defence.drivers == ["Defence outlay +9% YoY"]


def test_summarize_sectors_skips_sectorless_or_scoreless_rows():
    rows = [
        _row("A.NS", Action.BUY, Conviction.HIGH, 70, sector="Defence"),
        ScreenRow(symbol="NOSEC.NS", action=Action.BUY, score=80),  # no sector
        _row("B.NS", Action.HOLD, Conviction.LOW, None, sector="IT"),  # no score
    ]
    summaries = summarize_sectors(rows)
    assert [s.sector for s in summaries] == ["Defence"]


# --- Sector summary: multi-overlay (all government data) --------------------

def _macro_row(symbol, score, sector, signals):
    r = _row(symbol, Action.BUY, Conviction.HIGH, score, sector=sector)
    r.macro_signals = signals
    r.macro_points = round(sum(s.tailwind for s in signals), 1)
    r.budget_tailwind = next((s.tailwind for s in signals if s.kind == "budget"), None)
    return r


def test_summarize_sectors_ranks_by_combined_macro_tailwind():
    from indi_analyst.models import SectorMacroSignal as S

    cg = [S(kind="budget", label="Union Budget", sector="Capital Goods", tailwind=0.6, drivers=["Railways +75%"]),
          S(kind="iip", label="Industrial output (IIP)", sector="Capital Goods", tailwind=0.2, drivers=["IIP +3.5%"])]
    it = [S(kind="trade", label="Merchandise exports", sector="IT", tailwind=0.1, drivers=["Exports +2.5%"])]
    rows = [
        _macro_row("A.NS", 70, "Capital Goods", cg),
        _macro_row("B.NS", 60, "Capital Goods", cg),
        _macro_row("C.NS", 65, "IT", it),
    ]
    summaries = summarize_sectors(rows)
    assert [s.sector for s in summaries] == ["Capital Goods", "IT"]  # ranked by combined tailwind

    cg_sum = summaries[0]
    assert cg_sum.macro_tailwind == pytest.approx(0.4, abs=0.01)  # mean(0.6, 0.2)
    assert len(cg_sum.overlays) == 2
    assert cg_sum.budget_tailwind == 0.6  # budget still extractable
    assert len(cg_sum.drivers) == 2  # one driver per overlay


def test_min_macro_points_filter_and_macro_rank():
    from indi_analyst.models import SectorMacroSignal as S

    strong = _macro_row("STRONG.NS", 70, "Capital Goods",
                        [S(kind="budget", label="B", sector="Capital Goods", tailwind=0.8, drivers=["x"])])
    weak = _macro_row("WEAK.NS", 68, "IT",
                     [S(kind="trade", label="T", sector="IT", tailwind=0.05, drivers=["y"])])
    strong.macro_points, weak.macro_points = 4.0, 0.2

    kept = apply([strong, weak], ScreenFilter(min_macro_points=1.0))
    assert {r.symbol for r in kept} == {"STRONG.NS"}

    ranked = rank([weak, strong], by="macro")
    assert [r.symbol for r in ranked] == ["STRONG.NS", "WEAK.NS"]


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
