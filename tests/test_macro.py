"""Macro-overlay framework tests — aggregation, combined cap, scoring, backtest inertness."""

from __future__ import annotations

import pytest

from indi_analyst.analysis.macro import macro_score_delta, resolve_macro_signals
from indi_analyst.analysis.scoring import score
from indi_analyst.analysis.snapshot import build_snapshot
from indi_analyst.backtest.replay import snapshot_at
from indi_analyst.config import Settings
from indi_analyst.models import Fundamentals, SectorMacroSignal
from tests.conftest import MockPriceSource, make_ohlcv


def _settings(**over) -> Settings:
    base = {"default_llm_provider": "rulebased", "budget_year": "2026-27", "rate_pack_version": "2026"}
    base.update(over)
    return Settings(**base)


def _snap_for_sector(sector: str | None, **over):
    fund = Fundamentals(
        pe_ratio=22.0, roe=0.19, debt_to_equity=0.4, revenue_growth=0.18,
        profit_margin=0.16, sector=sector,
    )
    return build_snapshot(
        "TEST",
        settings=_settings(**over),
        price_source=MockPriceSource(make_ohlcv("up", n=300), fundamentals=fund),
        news_source=None,
    )


# --- Resolution: multiple overlays fire on one sector -----------------------

def test_capital_goods_gets_budget_and_rate():
    sigs = resolve_macro_signals("Capital Goods", _settings())
    kinds = {s.kind for s in sigs}
    assert "budget" in kinds and "rate" in kinds


def test_seed_macro_pack_status_is_visible_on_snapshot():
    snap = _snap_for_sector("Capital Goods")
    assert any(signal.fetched_at is None for signal in snap.macro_signals)
    assert any("unrefreshed seed data" in warning for warning in snap.warnings)


def test_unmapped_sector_yields_no_signals():
    assert resolve_macro_signals("Testing", _settings()) == []


# --- Combining under the shared cap -----------------------------------------

def test_combined_delta_is_capped():
    s = _settings()
    # Two maxed-out tailwinds would sum to budget_cap + rate_cap (5 + 4 = 9); the combined cap holds.
    signals = [
        SectorMacroSignal(kind="budget", label="Budget", sector="X", tailwind=1.0, drivers=["b"]),
        SectorMacroSignal(kind="rate", label="Rate", sector="X", tailwind=1.0, drivers=["r"]),
    ]
    delta, reasons = macro_score_delta(signals, s)
    assert delta == s.macro_max_points  # clamped to the combined cap, not 9
    assert any("capped" in r for r in reasons)


def test_opposing_signals_net_out():
    s = _settings()
    signals = [
        SectorMacroSignal(kind="budget", label="Budget", sector="X", tailwind=0.6, drivers=["b"]),
        SectorMacroSignal(kind="rate", label="Rate", sector="X", tailwind=-0.6, drivers=["r"]),
    ]
    delta, _ = macro_score_delta(signals, s)
    # +3.0 (0.6*5) and -2.4 (0.6*4) -> +0.6 net.
    assert delta == pytest.approx(0.6, abs=0.05)


def test_empty_signals_zero():
    assert macro_score_delta([], _settings()) == (0.0, [])


# --- Scoring integration ----------------------------------------------------

def test_macro_nudges_composite_up():
    snap = _snap_for_sector("Capital Goods")
    assert snap.macro_signals  # budget + rate fired
    q_with = score(snap)
    assert q_with.macro_adjustment > 0
    assert abs(q_with.macro_adjustment) <= Settings().macro_max_points
    assert any("tailwind" in r for r in q_with.reasons)

    snap.macro_signals = []  # same snapshot, overlays removed
    q_without = score(snap)
    assert q_with.score > q_without.score
    assert q_with.score - q_without.score == pytest.approx(q_with.macro_adjustment, abs=0.11)


def test_budget_signal_property_back_compat():
    snap = _snap_for_sector("Capital Goods")
    assert snap.budget_signal is not None and snap.budget_signal.kind == "budget"


def test_unmapped_sector_scores_inert():
    snap = _snap_for_sector("Testing")
    assert snap.macro_signals == []
    assert score(snap).macro_adjustment == 0.0


_ALL_OVERLAYS_OFF = {
    "budget_enabled": False, "rate_enabled": False, "iip_enabled": False, "gst_enabled": False,
    "credit_enabled": False, "trade_enabled": False, "inputcost_enabled": False, "monsoon_enabled": False,
}


def test_disabled_overlays_score_inert():
    snap = _snap_for_sector("Capital Goods", **_ALL_OVERLAYS_OFF)
    assert snap.macro_signals == []
    assert score(snap).macro_adjustment == 0.0


# --- Backtest stays inert (no look-ahead: no historical macro packs) --------

def test_backtest_snapshot_has_no_macro_signals():
    df = make_ohlcv("up", n=250)
    snap = snapshot_at(df, len(df) - 1)
    assert snap.macro_signals == []
    assert score(snap).macro_adjustment == 0.0
