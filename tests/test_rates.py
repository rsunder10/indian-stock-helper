"""RBI rate-cycle overlay tests — fully network-free (bundled pack)."""

from __future__ import annotations

import json

from indi_analyst.analysis.rates import load_rate_pack, resolve_rate_signal
from indi_analyst.config import Settings


def _settings(**over) -> Settings:
    base = {"default_llm_provider": "rulebased", "rate_pack_version": "2026"}
    base.update(over)
    return Settings(**base)


def test_bundled_rate_pack_loads():
    pack = load_rate_pack(_settings())
    assert pack is not None
    assert pack["repo_rate"] is not None and pack["sector_sensitivity"]


def test_easing_helps_rate_sensitive_sector():
    # Bundled pack: repo 6.0 < prev 6.5 -> easing (+1). Realty sensitivity 0.8 -> +0.8 tailwind.
    sig = resolve_rate_signal("Realty", _settings())
    assert sig is not None and sig.kind == "rate"
    assert sig.tailwind > 0
    assert "RBI repo" in sig.drivers[0]


def test_insensitive_sector_no_signal():
    # Healthcare sensitivity 0.0 -> no signal even in an easing regime.
    assert resolve_rate_signal("Healthcare", _settings()) is None


def test_unmapped_and_none_and_disabled():
    assert resolve_rate_signal("Nonexistent Sector", _settings()) is None
    assert resolve_rate_signal(None, _settings()) is None
    assert resolve_rate_signal("Realty", _settings(rate_enabled=False)) is None


def test_tightening_flips_the_sign(tmp_path):
    # An override pack in a tightening regime (repo above prev) must flip the tailwind negative.
    pack = load_rate_pack(_settings())
    hawkish = dict(pack, repo_rate=6.75, prev_repo_rate=6.5, regime="tightening")
    p = tmp_path / "rates_x.json"
    p.write_text(json.dumps(hawkish), encoding="utf-8")
    sig = resolve_rate_signal("Realty", _settings(rate_pack_version="x", rate_data_path=str(p)))
    assert sig is not None and sig.tailwind < 0
