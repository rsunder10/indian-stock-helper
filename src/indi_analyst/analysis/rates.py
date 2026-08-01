"""RBI interest-rate-cycle sector overlay. Deterministic, network-free, LLM-free.

The rate cycle is a dominant driver of Indian sector rotation: an easing cycle (RBI cutting the
repo rate) is a tailwind for rate-sensitive sectors (realty, autos, NBFCs, capital goods), a
tightening cycle a headwind; export-driven / defensive sectors are largely insensitive. This
module turns a stock's sector into a normalized tailwind from a bundled pack
(`data/rates_<version>.json`; refreshed at build time by `scripts/refresh_rates.py`). One instance
of the macro-overlay pattern (`analysis/macro.py`); missing/insensitive sectors contribute nothing.

    tailwind = regime_sign * sector_sensitivity        # regime_sign: easing +1, tightening -1
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from indi_analyst.analysis.sector_match import clamp, match_sector_key
from indi_analyst.config import Settings, get_settings
from indi_analyst.models import SectorMacroSignal

_PACK_CACHE: dict[tuple[str, str | None], dict | None] = {}


def load_rate_pack(settings: Settings | None = None) -> dict | None:
    """Load the rate pack. Cached; None if missing/malformed (the overlay simply goes quiet)."""
    settings = settings or get_settings()
    cache_key = (settings.rate_pack_version, settings.rate_data_path)
    if cache_key in _PACK_CACHE:
        return _PACK_CACHE[cache_key]

    text: str | None = None
    if settings.rate_data_path:
        p = Path(settings.rate_data_path).expanduser()
        if p.is_file():
            text = p.read_text(encoding="utf-8")
    else:
        resource = resources.files("indi_analyst").joinpath(f"data/rates_{settings.rate_pack_version}.json")
        if resource.is_file():
            with resource.open("r", encoding="utf-8") as fh:
                text = fh.read()

    pack: dict | None = None
    if text is not None:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("sector_sensitivity") and parsed.get("repo_rate") is not None:
                pack = parsed
        except (json.JSONDecodeError, ValueError):
            pack = None

    _PACK_CACHE[cache_key] = pack
    return pack


def _regime_sign(pack: dict) -> int:
    """+1 when easing (repo cut — bullish for rate-sensitive sectors), -1 tightening, 0 neutral.

    Derived from repo vs prev_repo; falls back to the stated `stance` when the level is unchanged.
    """
    repo = pack.get("repo_rate")
    prev = pack.get("prev_repo_rate")
    if repo is not None and prev is not None:
        if repo < prev:
            return 1
        if repo > prev:
            return -1
    stance = (pack.get("stance") or "").strip().lower()
    if stance in {"accommodative", "dovish"}:
        return 1
    if stance in {"hawkish", "tightening", "withdrawal of accommodation"}:
        return -1
    return 0


def resolve_rate_signal(
    sector: str | None, settings: Settings | None = None
) -> SectorMacroSignal | None:
    """Resolve a stock's sector to a rate-cycle overlay signal, or None if not applicable."""
    settings = settings or get_settings()
    if not settings.rate_enabled or not sector:
        return None

    pack = load_rate_pack(settings)
    if pack is None:
        return None

    sens_map = pack["sector_sensitivity"]
    matched_key = match_sector_key(sector, sens_map.keys())
    if matched_key is None:
        return None

    sensitivity = float(sens_map[matched_key])
    sign = _regime_sign(pack)
    tailwind = clamp(sign * sensitivity, -1.0, 1.0)
    if abs(tailwind) < 1e-9:  # neutral regime or insensitive sector — nothing to say
        return None

    regime = pack.get("regime") or ("easing" if sign > 0 else "tightening" if sign < 0 else "neutral")
    repo = pack.get("repo_rate")
    cpi = pack.get("cpi_yoy")
    detail = f"RBI repo {repo:.2f}% ({regime}"
    if cpi is not None:
        detail += f", CPI {cpi:.1f}%"
    detail += f") — {matched_key} is rate-sensitive"
    as_of = pack.get("as_of")

    return SectorMacroSignal(
        kind="rate",
        label=f"RBI rate cycle {as_of}".strip(),
        sector=matched_key,
        tailwind=round(tailwind, 3),
        drivers=[detail],
        citations=[pack["source"]] if pack.get("source") else [],
        as_of=as_of,
        fetched_at=pack.get("fetched_at"),
    )
