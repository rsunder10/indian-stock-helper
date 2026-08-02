"""Government-budget sector overlay. Deterministic, network-free, LLM-free.

The Union Budget expresses the government's spending priorities by head (Defence, Railways,
Green energy, Housing, ...). A head that is well funded / growing is a *structural, sector-level*
conviction signal — not a timing signal. This module turns a stock's sector into a normalized
tailwind, sourced entirely from a bundled pack (`data/budget_<year>.json`) so the analysis path
never makes a live call (see `scripts/refresh_budget.py` for the build-time fetch). It is one
instance of the general macro-overlay pattern (`analysis/macro.py`); missing data stays missing.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from indi_analyst.analysis.sector_match import clamp, match_sector_key
from indi_analyst.config import Settings, get_settings
from indi_analyst.models import SectorMacroSignal

# Parsed packs are cached by (year, override_path) — packs are static bundled data.
_PACK_CACHE: dict[tuple[str, str | None], dict | None] = {}


def load_budget_pack(settings: Settings | None = None) -> dict | None:
    """Load the budget pack for the configured year. Cached; None if missing/malformed.

    Resolution: an explicit `settings.budget_data_path` wins; otherwise the bundled
    `data/budget_<year>.json` shipped inside the package. A missing or unparseable pack
    degrades to None rather than raising — the overlay simply goes quiet.
    """
    settings = settings or get_settings()
    cache_key = (settings.budget_year, settings.budget_data_path)
    if cache_key in _PACK_CACHE:
        return _PACK_CACHE[cache_key]

    text: str | None = None
    if settings.budget_data_path:
        p = Path(settings.budget_data_path).expanduser()
        if p.is_file():
            text = p.read_text(encoding="utf-8")
    else:
        resource = resources.files("indi_analyst").joinpath(
            f"data/budget_{settings.budget_year}.json"
        )
        if resource.is_file():
            with resource.open("r", encoding="utf-8") as fh:
                text = fh.read()

    pack: dict | None = None
    if text is not None:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("heads") and parsed.get("sector_map"):
                pack = parsed
        except (json.JSONDecodeError, ValueError):
            pack = None

    _PACK_CACHE[cache_key] = pack
    return pack


def _lakh_crore(alloc_cr: float) -> str:
    """Format a rupee-crore figure compactly (₹6.8L cr for lakh-crore magnitudes)."""
    if alloc_cr >= 100_000:
        return f"₹{alloc_cr / 100_000:.1f}L cr"
    return f"₹{alloc_cr:,.0f} cr"


def resolve_budget_signal(
    sector: str | None, settings: Settings | None = None
) -> SectorMacroSignal | None:
    """Resolve a stock's sector to a budget overlay signal, or None if not applicable.

    Deterministic transform: `tailwind = clamp(mean(matched-head YoY%) / BUDGET_YOY_SCALE, -1, 1)`
    — a single transparent formula so every point traces to a published allocation number.
    None sector, no pack, or an unmapped sector all return None (missing stays missing).
    """
    settings = settings or get_settings()
    if not settings.budget_enabled or not sector:
        return None

    pack = load_budget_pack(settings)
    if pack is None:
        return None

    sector_map = pack["sector_map"]
    matched_key = match_sector_key(sector, sector_map.keys())
    if matched_key is None:
        return None

    heads_data = pack["heads"]
    present = [(h, heads_data[h]) for h in sector_map[matched_key] if h in heads_data]
    yoys = [float(hd.get("yoy_pct")) for _, hd in present if hd.get("yoy_pct") is not None]
    if not yoys:
        return None

    scale = settings.budget_yoy_scale or 20.0
    tailwind = clamp((sum(yoys) / len(yoys)) / scale, -1.0, 1.0)

    # Drivers: strongest heads first, each an auditable one-liner.
    ranked = sorted(present, key=lambda hp: hp[1].get("yoy_pct", 0.0), reverse=True)
    drivers: list[str] = []
    for name, hd in ranked[:3]:
        yoy = hd.get("yoy_pct")
        alloc = hd.get("alloc_cr")
        piece = f"{name} outlay {yoy:+.0f}% YoY" if yoy is not None else name
        if alloc is not None:
            piece += f" ({_lakh_crore(float(alloc))})"
        drivers.append(piece)

    budget_year = str(pack.get("budget_year", settings.budget_year))
    citations = [pack["source"]] if pack.get("source") else []

    return SectorMacroSignal(
        kind="budget",
        label=f"Union Budget {budget_year}",
        sector=matched_key,
        tailwind=round(tailwind, 3),
        drivers=drivers,
        citations=citations,
        as_of=budget_year,
        fetched_at=pack.get("fetched_at"),
    )
