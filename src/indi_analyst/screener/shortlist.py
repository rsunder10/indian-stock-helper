"""Comparative shortlist digest over the top-ranked rows.

Because the scan runs a full per-stock verdict, every `ScreenRow` already carries an
LLM-grounded thesis. The shortlist is therefore a *comparative overlay* — it assembles the
strongest ideas into one ranked digest from the numbers and theses that already exist, so it
stays grounded and never invents a pick. Deterministic and offline-safe by design.
"""

from __future__ import annotations

from indi_analyst.screener.filters import rank
from indi_analyst.screener.models import ScanResult, ScreenRow


def _fmt_row(i: int, r: ScreenRow) -> str:
    head = (
        f"{i}. {r.name or r.symbol} ({r.symbol}) — "
        f"{r.action.value if r.action else '—'}"
        f"{f', {r.conviction.value} conviction' if r.conviction else ''}, "
        f"score {r.score:.0f}/100"
    )
    if r.risk_reward is not None:
        head += f", R:R {r.risk_reward:.1f}:1"
    lead = r.thesis[0] if r.thesis else "See full analysis for the detailed thesis."
    return f"{head}\n   {lead}"


def shortlist_digest(result: ScanResult, *, n: int = 5) -> str:
    """Produce a ranked 'most compelling ideas' digest from the top-N scanned rows."""
    top = rank(result.ok_rows(), by="score")[:n]
    if not top:
        return "No scannable ideas in this universe (all symbols errored or filtered out)."

    lines = [
        f"Top {len(top)} ideas from {result.universe} "
        f"(scored deterministically, verdict via {result.provider}):",
        "",
    ]
    lines += [_fmt_row(i, r) for i, r in enumerate(top, 1)]
    return "\n".join(lines)
