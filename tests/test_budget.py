"""Budget overlay tests — fully network-free (bundled pack + mock source)."""

from __future__ import annotations

import pytest

from indi_analyst.analysis.budget import load_budget_pack, resolve_budget_signal
from indi_analyst.config import Settings

_YEAR = "2026-27"


def _settings(**over) -> Settings:
    base = {"default_llm_provider": "rulebased", "budget_year": _YEAR}
    base.update(over)
    return Settings(**base)


# --- Pack loading -----------------------------------------------------------

def test_bundled_pack_loads():
    pack = load_budget_pack(_settings())
    assert pack is not None
    assert pack["budget_year"] == _YEAR
    assert pack["heads"] and pack["sector_map"]


def test_missing_pack_returns_none():
    assert load_budget_pack(_settings(budget_year="1999-00")) is None


def test_malformed_pack_returns_none(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    assert load_budget_pack(_settings(budget_year="zzz", budget_data_path=str(bad))) is None


# --- Sector resolution + transform ------------------------------------------

def test_mapped_sector_positive_tailwind():
    sig = resolve_budget_signal("Power", _settings())
    assert sig is not None and sig.kind == "budget"
    # Green energy (22.0) + Infrastructure (10.1) -> mean 16.05 / 20 = 0.8025
    assert sig.tailwind == pytest.approx(0.803, abs=0.002)
    assert sig.as_of == _YEAR and "Union Budget" in sig.label
    assert sig.drivers and sig.citations


def test_yfinance_taxonomy_also_maps():
    # yfinance's coarse "Industrials" must resolve just like the NSE "Capital Goods".
    assert resolve_budget_signal("Industrials", _settings()) is not None


def test_unmapped_sector_is_none():
    assert resolve_budget_signal("Testing", _settings()) is None


def test_none_sector_is_none():
    assert resolve_budget_signal(None, _settings()) is None


def test_disabled_returns_none():
    assert resolve_budget_signal("Power", _settings(budget_enabled=False)) is None


def test_yoy_scale_tunes_tailwind():
    wide = resolve_budget_signal("Healthcare", _settings(budget_yoy_scale=40.0))
    tight = resolve_budget_signal("Healthcare", _settings(budget_yoy_scale=8.0))
    assert wide is not None and tight is not None
    assert tight.tailwind > wide.tailwind
    assert -1.0 <= tight.tailwind <= 1.0
