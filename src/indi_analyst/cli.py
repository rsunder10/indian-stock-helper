"""Minimal CLI for quick terminal analysis and smoke-testing the pipeline.

Usage:
    indi-analyst RELIANCE
    indi-analyst TCS --provider rulebased
    indi-analyst INFY --provider ollama
"""

from __future__ import annotations

import argparse
import sys

from indi_analyst.analysis.engine import analyze
from indi_analyst.models import Recommendation


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def render(rec: Recommendation) -> str:
    s, t, lv, v, q = rec.snapshot, rec.snapshot.technicals, rec.levels, rec.verdict, rec.quant
    lines: list[str] = []
    title = f"{s.name or s.symbol} ({s.symbol}, {s.exchange})"
    lines.append("=" * 68)
    lines.append(title)
    lines.append("=" * 68)
    lines.append(
        f"Last close: ₹{t.last_close:,.2f}"
        + (f"  ({_fmt_pct(t.change_pct)})" if t.change_pct is not None else "")
        + f"   Trend: {t.trend}"
    )
    lines.append("")
    lines.append(f"CALL: {rec.action.value}   Conviction: {rec.conviction.value}   "
                 f"Score: {q.score:.0f}/100 (tech {q.technical_score:.0f} / fund {q.fundamental_score:.0f})")
    lines.append(f"Analyst provider: {rec.provider}")
    lines.append("")
    lines.append("TRADE LEVELS")
    lines.append(f"  Entry : ₹{lv.entry_low:,.2f} – ₹{lv.entry_high:,.2f}")
    lines.append(f"  Stop  : ₹{lv.stop_loss:,.2f}  ({_fmt_pct(lv.stop_loss_pct)})")
    lines.append(f"  T1    : ₹{lv.target_1:,.2f}  ({_fmt_pct(lv.target_1_pct)})")
    lines.append(f"  T2    : ₹{lv.target_2:,.2f}  ({_fmt_pct(lv.target_2_pct)})")
    lines.append(f"  R:R   : {lv.risk_reward:.2f} : 1")
    lines.append("")
    lines.append("THESIS")
    for b in v.thesis:
        lines.append(f"  • {b}")
    if v.key_risks:
        lines.append("")
        lines.append("KEY RISKS")
        for r in v.key_risks:
            lines.append(f"  • {r}")
    if v.catalysts:
        lines.append("")
        lines.append("CATALYSTS")
        for c in v.catalysts:
            lines.append(f"  • {c}")
    if v.summary:
        lines.append("")
        lines.append("GIST")
        lines.append(f"  {v.summary}")
    if s.warnings:
        lines.append("")
        lines.append("NOTES")
        for w in s.warnings:
            lines.append(f"  ! {w}")
    lines.append("")
    lines.append(rec.disclaimer)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze an Indian (NSE/BSE) stock.")
    parser.add_argument("query", help="Ticker or symbol, e.g. RELIANCE, TCS, INFY.NS")
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM provider: ollama | anthropic | openai | gemini | rulebased "
        "(default: from config / .env).",
    )
    args = parser.parse_args(argv)

    try:
        rec = analyze(args.query, provider=args.provider)
    except Exception as e:  # data errors, bad ticker, etc.
        print(f"Error analyzing '{args.query}': {e}", file=sys.stderr)
        return 1

    print(render(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
