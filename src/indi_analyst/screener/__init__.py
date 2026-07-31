"""Screener / recommender: scan a universe of stocks and surface the best ideas.

Phase 1 of the roadmap — go from "analyze this stock" to "which stocks should I look at?".
Layers on top of the deterministic engine without touching it: each stock is run through the
same `build_snapshot` + `analyze_snapshot` pipeline, then flattened into a rankable `ScreenRow`.
"""

from __future__ import annotations

from indi_analyst.screener.batch import scan_universe
from indi_analyst.screener.filters import apply, rank, resolve_preset
from indi_analyst.screener.models import (
    PRESETS,
    Constituent,
    ScanResult,
    ScreenFilter,
    ScreenRow,
    SectorSummary,
)
from indi_analyst.screener.sectors import summarize_sectors
from indi_analyst.screener.shortlist import shortlist_digest
from indi_analyst.screener.universe import load_universe

__all__ = [
    "scan_universe",
    "load_universe",
    "shortlist_digest",
    "summarize_sectors",
    "apply",
    "rank",
    "resolve_preset",
    "PRESETS",
    "Constituent",
    "ScanResult",
    "ScreenFilter",
    "ScreenRow",
    "SectorSummary",
]
