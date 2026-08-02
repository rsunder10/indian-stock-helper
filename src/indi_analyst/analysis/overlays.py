"""Generic "national-indicator × sector-sensitivity" macro overlays. Deterministic, network-free.

Several government-open-data series share one shape: a single national headline number (IIP growth,
GST-collection YoY, bank-credit growth, export growth, WPI input inflation, monsoon rainfall vs the
long-period average) that helps or hurts a stock's sector *in proportion to how exposed the sector
is* to that number. That is exactly the rate-overlay shape (`analysis/rates.py`: regime × sensitivity)
generalized, so instead of one bespoke module per dataset we drive them all from a small `OverlaySpec`
plus a shared bundled pack (`data/<kind>_<version>.json`, build-time refresh: scripts/refresh_macro.py).

    strength = clamp((value - neutral) / scale, -1, +1)     # how far the number is from its baseline
    tailwind = clamp(direction * strength * sensitivity, -1, +1)   # direction: +1 helps, -1 (costs) hurts

Every value traces to one published figure and one maintained sensitivity weight — no black boxes. This
is one instance of the macro-overlay pattern (`analysis/macro.py`); missing/insensitive sectors, a
disabled overlay, or an absent pack all contribute nothing (missing stays missing).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from indi_analyst.analysis.sector_match import clamp, match_sector_key
from indi_analyst.config import Settings, get_settings
from indi_analyst.models import SectorMacroSignal


@dataclass(frozen=True)
class OverlaySpec:
    """Static definition of one national-indicator overlay (the parts that never come from a fetch)."""

    kind: str  # source id, e.g. "iip" — also the pack filename stem and the SectorMacroSignal.kind
    label: str  # human label prefix, e.g. "Industrial output (IIP)"
    unit: str  # display unit for the headline number, e.g. "% YoY"
    direction: (
        int  # +1 when a higher number helps sensitive sectors, -1 when it hurts (input costs)
    )
    rising_word: str  # phrase when the number is above baseline, e.g. "expanding"
    falling_word: str  # phrase when below baseline, e.g. "cooling"
    exposure_word: str  # how the sector relates, e.g. "is cyclical", "is consumption-led"
    version_attr: str  # Settings attr holding the pack version, e.g. "iip_pack_version"
    enabled_attr: str  # Settings bool attr, e.g. "iip_enabled"
    path_attr: str  # Settings override-path attr, e.g. "iip_data_path"
    scale_attr: (
        str  # Settings float attr: the (value-neutral) that maps to a full ±1, e.g. "iip_scale"
    )
    max_attr: str  # Settings float attr: per-source point cap, e.g. "iip_max_points"


# The registry of generic overlays. Adding a government series = one entry here + a bundled pack +
# five Settings fields + (optionally) a refresh_macro.py fetch. Order is stable for deterministic output.
SENSITIVITY_OVERLAYS: list[OverlaySpec] = [
    OverlaySpec(
        kind="iip",
        label="Industrial output (IIP)",
        unit="% YoY",
        direction=1,
        rising_word="expanding",
        falling_word="contracting",
        exposure_word="is cyclical",
        version_attr="iip_pack_version",
        enabled_attr="iip_enabled",
        path_attr="iip_data_path",
        scale_attr="iip_scale",
        max_attr="iip_max_points",
    ),
    OverlaySpec(
        kind="gst",
        label="GST collections",
        unit="% YoY",
        direction=1,
        rising_word="rising",
        falling_word="softening",
        exposure_word="is consumption-led",
        version_attr="gst_pack_version",
        enabled_attr="gst_enabled",
        path_attr="gst_data_path",
        scale_attr="gst_scale",
        max_attr="gst_max_points",
    ),
    OverlaySpec(
        kind="credit",
        label="Bank credit growth",
        unit="% YoY",
        direction=1,
        rising_word="accelerating",
        falling_word="slowing",
        exposure_word="is credit-driven",
        version_attr="credit_pack_version",
        enabled_attr="credit_enabled",
        path_attr="credit_data_path",
        scale_attr="credit_scale",
        max_attr="credit_max_points",
    ),
    OverlaySpec(
        kind="trade",
        label="Merchandise exports",
        unit="% YoY",
        direction=1,
        rising_word="growing",
        falling_word="declining",
        exposure_word="is export-oriented",
        version_attr="trade_pack_version",
        enabled_attr="trade_enabled",
        path_attr="trade_data_path",
        scale_attr="trade_scale",
        max_attr="trade_max_points",
    ),
    OverlaySpec(
        kind="inputcost",
        label="Input-cost inflation (WPI)",
        unit="% YoY",
        direction=-1,
        rising_word="rising",
        falling_word="easing",
        exposure_word="is input-cost-sensitive",
        version_attr="inputcost_pack_version",
        enabled_attr="inputcost_enabled",
        path_attr="inputcost_data_path",
        scale_attr="inputcost_scale",
        max_attr="inputcost_max_points",
    ),
    OverlaySpec(
        kind="monsoon",
        label="Monsoon rainfall",
        unit="% vs LPA",
        direction=1,
        rising_word="above-normal",
        falling_word="deficient",
        exposure_word="is rural-demand-led",
        version_attr="monsoon_pack_version",
        enabled_attr="monsoon_enabled",
        path_attr="monsoon_data_path",
        scale_attr="monsoon_scale",
        max_attr="monsoon_max_points",
    ),
]

# Parsed packs cached by (kind, version, override_path) — bundled data is static.
_PACK_CACHE: dict[tuple[str, str, str | None], dict | None] = {}


def load_overlay_pack(spec: OverlaySpec, settings: Settings | None = None) -> dict | None:
    """Load `spec`'s bundled pack. Cached; None if missing/malformed (the overlay goes quiet)."""
    settings = settings or get_settings()
    version = str(getattr(settings, spec.version_attr))
    override = getattr(settings, spec.path_attr)
    cache_key = (spec.kind, version, override)
    if cache_key in _PACK_CACHE:
        return _PACK_CACHE[cache_key]

    text: str | None = None
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            text = p.read_text(encoding="utf-8")
    else:
        resource = resources.files("indi_analyst").joinpath(f"data/{spec.kind}_{version}.json")
        if resource.is_file():
            with resource.open("r", encoding="utf-8") as fh:
                text = fh.read()

    pack: dict | None = None
    if text is not None:
        try:
            parsed = json.loads(text)
            if (
                isinstance(parsed, dict)
                and parsed.get("sector_sensitivity")
                and parsed.get("value") is not None
            ):
                pack = parsed
        except (json.JSONDecodeError, ValueError):
            pack = None

    _PACK_CACHE[cache_key] = pack
    return pack


def resolve_overlay_signal(
    spec: OverlaySpec, sector: str | None, settings: Settings | None = None
) -> SectorMacroSignal | None:
    """Resolve one national-indicator overlay for a stock's sector, or None if not applicable."""
    settings = settings or get_settings()
    if not getattr(settings, spec.enabled_attr) or not sector:
        return None

    pack = load_overlay_pack(spec, settings)
    if pack is None:
        return None

    sens_map = pack["sector_sensitivity"]
    matched_key = match_sector_key(sector, sens_map.keys())
    if matched_key is None:
        return None

    try:
        sensitivity = float(sens_map[matched_key])
        value = float(pack["value"])
    except (TypeError, ValueError):
        return None
    if abs(sensitivity) < 1e-9:  # sector present but explicitly insensitive — nothing to say
        return None

    neutral = float(pack.get("neutral", 0.0))
    scale = float(getattr(settings, spec.scale_attr)) or 1.0
    strength = clamp((value - neutral) / scale, -1.0, 1.0)
    tailwind = clamp(spec.direction * strength * sensitivity, -1.0, 1.0)
    if abs(tailwind) < 1e-9:  # number sitting on its baseline — no nudge
        return None

    unit = pack.get("unit", spec.unit)
    trend_word = spec.rising_word if value >= neutral else spec.falling_word
    as_of = pack.get("as_of")
    driver = (
        f"{spec.label} {value:+.1f}{unit} ({trend_word} vs {neutral:.1f} baseline)"
        f" — {matched_key} {spec.exposure_word}"
    )

    return SectorMacroSignal(
        kind=spec.kind,
        label=f"{spec.label} {as_of}".strip(),
        sector=matched_key,
        tailwind=round(tailwind, 3),
        drivers=[driver],
        citations=[pack["source"]] if pack.get("source") else [],
        as_of=as_of,
        fetched_at=pack.get("fetched_at"),
    )
