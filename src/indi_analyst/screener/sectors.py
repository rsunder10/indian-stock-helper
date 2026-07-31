"""Top-down sector view: aggregate scanned rows into a budget-aware sector ranking.

Pure function over already-scanned `ScreenRow`s (like `filters.py`). Answers "which sector"
by pairing the per-sector budget tailwind (a constant from the pack) with the bottom-up average
score of that sector's stocks, so the workflow is: rank sectors -> filter the scan to a favoured
sector -> pick the accumulate candidates.
"""

from __future__ import annotations

from indi_analyst.screener.models import ScreenRow, SectorSummary


def summarize_sectors(rows: list[ScreenRow], *, top_symbols: int = 3) -> list[SectorSummary]:
    """Group ok rows by sector and rank sectors by budget tailwind, then average score.

    Rows without a sector or a score are ignored for the aggregate. Ranking puts the strongest
    government-budget tailwind first (unmapped sectors last), breaking ties by average score —
    a top-down "where is the macro wind at my back?" ordering.
    """
    buckets: dict[str, list[ScreenRow]] = {}
    for r in rows:
        if not r.ok or not r.sector or r.score is None:
            continue
        buckets.setdefault(r.sector, []).append(r)

    summaries: list[SectorSummary] = []
    for sector, group in buckets.items():
        ranked = sorted(group, key=lambda r: r.score, reverse=True)
        scores = [r.score for r in ranked]
        tailwinds = [r.budget_tailwind for r in ranked if r.budget_tailwind is not None]
        drivers = next((r.budget_drivers for r in ranked if r.budget_drivers), [])
        summaries.append(
            SectorSummary(
                sector=sector,
                budget_tailwind=tailwinds[0] if tailwinds else None,
                n_stocks=len(ranked),
                avg_score=round(sum(scores) / len(scores), 1),
                top_symbols=[r.symbol for r in ranked[:top_symbols]],
                drivers=list(drivers),
            )
        )

    # Strongest tailwind first (unmapped sectors sink), then higher average score.
    summaries.sort(
        key=lambda s: (
            s.budget_tailwind if s.budget_tailwind is not None else float("-inf"),
            s.avg_score if s.avg_score is not None else float("-inf"),
        ),
        reverse=True,
    )
    return summaries
