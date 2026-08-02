"""Screen and rank `ScreenRow`s. Pure functions over already-scanned rows."""

from __future__ import annotations

from indi_analyst.screener.models import PRESETS, ScreenFilter, ScreenRow

_RANK_KEYS = {
    "score": lambda r: r.score,
    "risk_reward": lambda r: r.risk_reward,
    "change_pct": lambda r: r.change_pct,
    "technical_score": lambda r: r.technical_score,
    "fundamental_score": lambda r: r.fundamental_score,
    "macro": lambda r: r.macro_points,
    "macro_tailwind": lambda r: r.macro_tailwind,
}


def apply(rows: list[ScreenRow], flt: ScreenFilter | None) -> list[ScreenRow]:
    """Return rows passing the filter (errored rows are always dropped)."""
    if flt is None:
        return [r for r in rows if r.ok]
    return [r for r in rows if flt.matches(r)]


def rank(rows: list[ScreenRow], by: str = "score", descending: bool = True) -> list[ScreenRow]:
    """Sort rows by a numeric field, pushing missing values to the bottom."""
    key = _RANK_KEYS.get(by)
    if key is None:
        raise ValueError(f"Cannot rank by '{by}'. Options: {sorted(_RANK_KEYS)}.")
    worst = float("-inf") if descending else float("inf")
    return sorted(rows, key=lambda r: key(r) if key(r) is not None else worst, reverse=descending)


def resolve_preset(name: str) -> ScreenFilter:
    """Look up a named preset (case-insensitive)."""
    key = name.strip().lower()
    if key not in PRESETS:
        raise ValueError(f"Unknown preset '{name}'. Options: {sorted(PRESETS)}.")
    return PRESETS[key]
