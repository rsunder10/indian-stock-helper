"""Macro overlays: the framework that turns sector-keyed government-open-data signals into a
small, bounded, explainable nudge on the quant score.

Each source (budget, interest-rate cycle, …) is a resolver that maps a stock's sector to a
`SectorMacroSignal` from a bundled, build-time-refreshed pack. This module is the single place
that (a) runs every registered resolver over a sector, and (b) combines their per-source point
deltas under a **shared cap** so multiple overlays can never blow up the calibrated, 50-centred
score. Adding a new government dataset = write a resolver + pack and register it here.

Determinism / no look-ahead: every signal is keyed on the stock's *current* sector, and the
backtest replays snapshots with an empty `Fundamentals` (no sector), so all overlays go inert
there automatically — the framework preserves the technical-only backtest guarantee for free.
"""

from __future__ import annotations

from typing import Callable

from indi_analyst.analysis.budget import resolve_budget_signal
from indi_analyst.analysis.rates import resolve_rate_signal
from indi_analyst.analysis.sector_match import clamp
from indi_analyst.config import Settings, get_settings
from indi_analyst.models import SectorMacroSignal

# Registered resolvers, in a stable order. Each maps (sector, settings) -> signal | None.
_RESOLVERS: list[Callable[..., SectorMacroSignal | None]] = [
    resolve_budget_signal,
    resolve_rate_signal,
]


def resolve_macro_signals(
    sector: str | None, settings: Settings | None = None
) -> list[SectorMacroSignal]:
    """Run every registered overlay over a sector; return the ones that fired (order stable)."""
    settings = settings or get_settings()
    out: list[SectorMacroSignal] = []
    for resolve in _RESOLVERS:
        sig = resolve(sector, settings)
        if sig is not None:
            out.append(sig)
    return out


def _per_source_cap(kind: str, settings: Settings) -> float:
    return {
        "budget": settings.budget_max_points,
        "rate": settings.rate_max_points,
    }.get(kind, 0.0)


def macro_score_delta(
    signals: list[SectorMacroSignal], settings: Settings | None = None
) -> tuple[float, list[str]]:
    """Combine macro signals into one bounded, signed point nudge + human-readable reasons.

    Each signal contributes `clamp(tailwind * per-source cap, ±cap)`; the sum is then clamped to
    `MACRO_MAX_POINTS` so the overlays together stay small relative to the technical/fundamental
    core. Returns (total_delta, reasons); reasons is empty when the nudge rounds to ~0.
    """
    settings = settings or get_settings()
    reasons: list[str] = []
    raw = 0.0
    for s in signals:
        cap = _per_source_cap(s.kind, settings)
        d = round(clamp(s.tailwind * cap, -cap, cap), 1)
        if abs(d) < 0.05:
            continue
        raw += d
        word = "tailwind" if d > 0 else "headwind"
        driver = s.drivers[0] if s.drivers else s.sector
        reasons.append(f"{d:+.1f} pts — {s.label} sector {word}: {driver}.")

    total = round(clamp(raw, -settings.macro_max_points, settings.macro_max_points), 1)
    if abs(total) < 0.05:
        return 0.0, []
    if reasons and abs(raw) > settings.macro_max_points:
        reasons.append(f"(combined macro nudge capped at ±{settings.macro_max_points:.0f} pts)")
    return total, reasons
