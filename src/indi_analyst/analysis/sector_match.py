"""Shared sector-key matching for the macro overlays (budget, rate, …).

Every overlay maps a stock's `sector` against a pack keyed by sector strings. The keys cover both
taxonomies a sector may arrive in — NSE "Industry" strings (bundled universe CSVs) and the coarser
yfinance GICS-like sectors — so matching must be forgiving: exact (case-insensitive) first, then a
substring match either way (mirrors `ScreenFilter.sectors`). Insertion order makes the substring
fallback deterministic.
"""

from __future__ import annotations

from typing import Iterable


def match_sector_key(sector: str | None, keys: Iterable[str]) -> str | None:
    """Return the mapping key that best matches `sector`, or None."""
    sec = (sector or "").strip().lower()
    if not sec:
        return None
    keys = list(keys)
    for k in keys:
        if k.strip().lower() == sec:
            return k
    for k in keys:
        kk = k.strip().lower()
        if kk and (kk in sec or sec in kk):
            return k
    return None


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
