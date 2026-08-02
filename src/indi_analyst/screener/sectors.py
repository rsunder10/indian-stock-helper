"""Top-down sector view: aggregate scanned rows into a macro-aware sector ranking.

Pure function over already-scanned `ScreenRow`s (like `filters.py`). Answers "which sector" by
pairing the per-sector macro overlays (budget, rate, IIP, GST, credit, trade, input-cost, monsoon —
constants across a sector's stocks) with the bottom-up average score of that sector's stocks, so the
workflow is: rank sectors -> filter the scan to a favoured sector -> pick the accumulate candidates.

`macro_tailwind` is the mean of every overlay's tailwind for the sector (a single "is the macro wind
at my back?" number); `budget_tailwind` is retained as a back-compatible single-source view.
"""

from __future__ import annotations

from indi_analyst.screener.models import ScreenRow, SectorSummary


def _representative_signals(rows: list[ScreenRow]):
    """The macro overlays for the sector — a per-sector constant, so take the first row that has them."""
    return next((r.macro_signals for r in rows if r.macro_signals), [])


def summarize_sectors(rows: list[ScreenRow], *, top_symbols: int = 3) -> list[SectorSummary]:
    """Group ok rows by sector and rank sectors by combined macro tailwind, then average score.

    Rows without a sector or a score are ignored for the aggregate. Ranking puts the strongest
    combined government-data tailwind first (unmapped sectors last), breaking ties by average score —
    a top-down "where is the macro wind at my back?" ordering. Falls back to the budget tailwind when
    a row predates the multi-overlay fields (older cache), so older scans still rank sensibly.
    """
    buckets: dict[str, list[ScreenRow]] = {}
    for r in rows:
        if not r.ok or not r.sector or r.score is None:
            continue
        buckets.setdefault(r.sector, []).append(r)

    summaries: list[SectorSummary] = []
    for sector, group in buckets.items():
        # Every row in a bucket already has a non-None score (filtered above); make that explicit
        # for the sort key and the average so the types stay float, not float | None.
        ranked = sorted(group, key=lambda r: r.score if r.score is not None else 0.0, reverse=True)
        scores = [s for r in ranked if (s := r.score) is not None]

        signals = _representative_signals(ranked)
        if signals:
            macro_tailwind = round(sum(s.tailwind for s in signals) / len(signals), 3)
            overlays = [s.label for s in signals]
            drivers = [s.drivers[0] for s in signals if s.drivers]
        else:  # older cached rows: fall back to the single budget field
            tailwinds = [r.budget_tailwind for r in ranked if r.budget_tailwind is not None]
            macro_tailwind = tailwinds[0] if tailwinds else None
            overlays = []
            drivers = next((r.budget_drivers for r in ranked if r.budget_drivers), [])

        budget_tw = next(
            (s.tailwind for s in signals if s.kind == "budget"),
            next((r.budget_tailwind for r in ranked if r.budget_tailwind is not None), None),
        )
        summaries.append(
            SectorSummary(
                sector=sector,
                macro_tailwind=macro_tailwind,
                overlays=overlays,
                budget_tailwind=budget_tw,
                n_stocks=len(ranked),
                avg_score=round(sum(scores) / len(scores), 1),
                top_symbols=[r.symbol for r in ranked[:top_symbols]],
                drivers=list(drivers),
            )
        )

    # Strongest combined tailwind first (unmapped sectors sink), then higher average score.
    summaries.sort(
        key=lambda s: (
            s.macro_tailwind if s.macro_tailwind is not None else float("-inf"),
            s.avg_score if s.avg_score is not None else float("-inf"),
        ),
        reverse=True,
    )
    return summaries
