"""National-indicator overlay tests (IIP, GST, credit, trade, input-cost, monsoon) — network-free.

These exercise the generic engine in `analysis/overlays.py` plus the `national_context` strip. All
data comes from the bundled seed packs or tmp override packs; no live government API is ever called.
"""

from __future__ import annotations

import json

from indi_analyst.analysis.macro import national_context, resolve_macro_signals
from indi_analyst.analysis.overlays import (
    SENSITIVITY_OVERLAYS,
    load_overlay_pack,
    resolve_overlay_signal,
)
from indi_analyst.config import Settings

_SPEC = {s.kind: s for s in SENSITIVITY_OVERLAYS}


def _settings(**over) -> Settings:
    base = {"default_llm_provider": "rulebased"}
    base.update(over)
    return Settings(**base)


def test_all_bundled_packs_load_and_are_well_formed():
    s = _settings()
    for spec in SENSITIVITY_OVERLAYS:
        pack = load_overlay_pack(spec, s)
        assert pack is not None, f"{spec.kind} pack missing"
        assert pack["sector_sensitivity"] and pack.get("value") is not None


def test_iip_helps_cyclical_sector():
    # Bundled IIP value 3.5 > neutral 3.0 -> mild expansion; Capital Goods sensitivity 0.9 -> tailwind > 0.
    sig = resolve_overlay_signal(_SPEC["iip"], "Capital Goods", _settings())
    assert sig is not None and sig.kind == "iip" and sig.tailwind > 0
    assert "Industrial output" in sig.drivers[0]


def test_inputcost_is_a_headwind_for_consumers_but_tailwind_for_producers():
    # direction=-1: WPI 2.8 > neutral 2.0. Auto sensitivity +0.8 -> headwind; Metals -0.6 -> tailwind.
    consumer = resolve_overlay_signal(
        _SPEC["inputcost"], "Automobile and Auto Components", _settings()
    )
    producer = resolve_overlay_signal(_SPEC["inputcost"], "Metals & Mining", _settings())
    assert consumer is not None and consumer.tailwind < 0
    assert producer is not None and producer.tailwind > 0


def test_insensitive_sector_yields_no_signal():
    # IIP Healthcare sensitivity 0.0 -> nothing to say.
    assert resolve_overlay_signal(_SPEC["iip"], "Healthcare", _settings()) is None


def test_unmapped_none_and_disabled():
    assert resolve_overlay_signal(_SPEC["gst"], "Nonexistent Sector", _settings()) is None
    assert resolve_overlay_signal(_SPEC["gst"], None, _settings()) is None
    assert (
        resolve_overlay_signal(
            _SPEC["gst"], "Fast Moving Consumer Goods", _settings(gst_enabled=False)
        )
        is None
    )


def test_scale_controls_magnitude():
    tight = resolve_overlay_signal(_SPEC["iip"], "Capital Goods", _settings(iip_scale=2.0))
    wide = resolve_overlay_signal(_SPEC["iip"], "Capital Goods", _settings(iip_scale=16.0))
    assert tight is not None and wide is not None
    assert abs(tight.tailwind) > abs(wide.tailwind)  # a tighter scale amplifies the same gap


def test_value_on_baseline_is_inert(tmp_path):
    pack = load_overlay_pack(_SPEC["iip"], _settings())
    flat = dict(pack, value=pack["neutral"])  # exactly on the zero-nudge baseline
    p = tmp_path / "iip_x.json"
    p.write_text(json.dumps(flat), encoding="utf-8")
    sig = resolve_overlay_signal(
        _SPEC["iip"], "Capital Goods", _settings(iip_pack_version="x", iip_data_path=str(p))
    )
    assert sig is None


def test_missing_pack_degrades_to_none():
    assert load_overlay_pack(_SPEC["iip"], _settings(iip_pack_version="does-not-exist")) is None


def test_multiple_new_overlays_fire_for_one_sector():
    kinds = {s.kind for s in resolve_macro_signals("Fast Moving Consumer Goods", _settings())}
    # FMCG is consumption/rural-led: at least GST and monsoon should register alongside budget/rate.
    assert {"gst", "monsoon"} <= kinds


def test_national_context_lists_rate_and_every_enabled_overlay():
    lines = national_context(_settings())
    assert any("RBI repo" in ln for ln in lines)
    for spec in SENSITIVITY_OVERLAYS:
        assert any(spec.label in ln for ln in lines), f"{spec.kind} missing from national strip"
    assert any("seed/unrefreshed" in ln for ln in lines)


def test_national_context_skips_disabled_overlay():
    lines = national_context(_settings(iip_enabled=False))
    assert not any(_SPEC["iip"].label in ln for ln in lines)
