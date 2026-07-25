"""Minimal CLI for quick terminal analysis and smoke-testing the pipeline.

Usage:
    indi-analyst RELIANCE                     # single-stock deep dive (default)
    indi-analyst TCS --provider rulebased
    indi-analyst analyze INFY --provider ollama

    indi-analyst screen --universe nifty50 --provider rulebased --top 15
    indi-analyst screen --universe nifty50 --preset high-conviction-buys --digest
"""

from __future__ import annotations

import argparse
import sys

from indi_analyst.analysis.engine import analyze
from indi_analyst.models import Action, Recommendation
from indi_analyst.screener import (
    resolve_preset,
    scan_universe,
    shortlist_digest,
)
from indi_analyst.screener.filters import apply, rank
from indi_analyst.screener.models import ScanResult, ScreenFilter


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


def render_scan(result: ScanResult, rows: list, top: int | None) -> str:
    """A compact ranked table of scan rows (already filtered/ranked by the caller)."""
    shown = rows[:top] if top else rows
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(
        f"SCREEN: {result.universe}   provider: {result.provider}   "
        f"scanned {result.ok_count} ok / {result.error_count} err   showing {len(shown)}"
    )
    lines.append("=" * 78)
    header = f"{'#':>2}  {'SYMBOL':<14}{'ACTION':<11}{'CONV':<7}{'SCORE':>6}{'CLOSE':>11}{'R:R':>6}  SECTOR"
    lines.append(header)
    lines.append("-" * 78)
    for i, r in enumerate(shown, 1):
        lines.append(
            f"{i:>2}  {r.symbol:<14}"
            f"{(r.action.value if r.action else '—'):<11}"
            f"{(r.conviction.value if r.conviction else '—'):<7}"
            f"{(f'{r.score:.0f}' if r.score is not None else '—'):>6}"
            f"{(f'₹{r.last_close:,.0f}' if r.last_close is not None else '—'):>11}"
            f"{(f'{r.risk_reward:.1f}' if r.risk_reward is not None else '—'):>6}"
            f"  {(r.sector or '')[:26]}"
        )
    if not shown:
        lines.append("  (no rows matched the filter)")
    for w in result.warnings:
        lines.append(f"! {w}")
    return "\n".join(lines)


def _build_filter(args) -> ScreenFilter | None:
    """Assemble a ScreenFilter from CLI flags, starting from a preset if given."""
    flt = resolve_preset(args.preset) if args.preset else ScreenFilter()
    data = flt.model_dump()
    if args.min_score is not None:
        data["min_score"] = args.min_score
    if args.min_rr is not None:
        data["min_rr"] = args.min_rr
    if args.max_pe is not None:
        data["max_pe"] = args.max_pe
    if args.action:
        data["actions"] = {Action(a.strip().upper()) for a in args.action.split(",")}
    if args.sector:
        data["sectors"] = {s.strip() for s in args.sector.split(",")}
    flt = ScreenFilter(**data)
    # An all-None filter means "no filter" — pass None so errored rows still drop cleanly.
    return flt if flt.model_dump(exclude_none=True) else None


def _cmd_analyze(args) -> int:
    try:
        rec = analyze(args.query, provider=args.provider)
    except Exception as e:  # data errors, bad ticker, etc.
        print(f"Error analyzing '{args.query}': {e}", file=sys.stderr)
        return 1
    print(render(rec))
    return 0


def _cmd_screen(args) -> int:
    def _progress(done: int, total: int, symbol: str) -> None:
        print(f"\rScanning {done}/{total} … {symbol:<16}", end="", file=sys.stderr, flush=True)

    use_cache = not args.no_cache

    try:
        result = scan_universe(
            args.universe,
            provider=args.provider,
            limit=args.limit,
            use_cache=use_cache,
            on_progress=None if args.format == "json" else _progress,
        )
    except Exception as e:
        print(f"Error scanning '{args.universe}': {e}", file=sys.stderr)
        return 1
    if args.format != "json":
        print("", file=sys.stderr)  # end the progress line

    flt = _build_filter(args)
    rows = rank(apply(result.rows, flt), by="score")

    if args.format == "json":
        import json

        payload = {
            "universe": result.universe,
            "provider": result.provider,
            "scanned_at": result.scanned_at.isoformat(),
            "rows": [r.model_dump(mode="json") for r in (rows[: args.top] if args.top else rows)],
            "warnings": result.warnings,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print(render_scan(result, rows, args.top))
    if args.digest:
        digest_result = ScanResult(
            universe=result.universe, provider=result.provider, rows=rows, warnings=[]
        )
        print("\n" + shortlist_digest(digest_result, n=args.top or 5))
    return 0


# Subcommands whose names are reserved; anything else is treated as an `analyze` query.
_SUBCOMMANDS = {"analyze", "screen"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="indi-analyst", description="Analyze or screen Indian (NSE/BSE) stocks."
    )
    sub = parser.add_subparsers(dest="command")

    p_an = sub.add_parser("analyze", help="Deep-dive a single stock (default).")
    p_an.add_argument("query", help="Ticker or symbol, e.g. RELIANCE, TCS, INFY.NS")
    p_an.add_argument("--provider", default=None,
                      help="LLM provider: ollama | anthropic | openai | gemini | rulebased.")
    p_an.set_defaults(func=_cmd_analyze)

    p_sc = sub.add_parser("screen", help="Scan a universe and rank the best ideas.")
    p_sc.add_argument("--universe", default="nifty50",
                      help="nifty50 | nifty200 | nifty500 | watchlist:SYM1,SYM2 | file:/path.csv")
    p_sc.add_argument("--provider", default=None, help="LLM provider (default: config).")
    p_sc.add_argument("--top", type=int, default=15, help="Show only the top N rows (0 = all).")
    p_sc.add_argument("--limit", type=int, default=None, help="Scan at most N constituents.")
    p_sc.add_argument("--preset", default=None,
                      help="high-conviction-buys | oversold-quality | breakout-with-fundamentals")
    p_sc.add_argument("--min-score", type=float, default=None)
    p_sc.add_argument("--min-rr", type=float, default=None, help="Minimum risk:reward.")
    p_sc.add_argument("--max-pe", type=float, default=None)
    p_sc.add_argument("--action", default=None, help="Comma list, e.g. BUY,ACCUMULATE.")
    p_sc.add_argument("--sector", default=None, help="Comma list of sector substrings.")
    p_sc.add_argument("--digest", action="store_true", help="Append a top-ideas digest.")
    p_sc.add_argument("--no-cache", action="store_true", help="Bypass the snapshot cache.")
    p_sc.add_argument("--format", choices=["table", "json"], default="table")
    p_sc.set_defaults(func=_cmd_screen, top=15)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Back-compat: `indi-analyst RELIANCE [flags]` with no subcommand -> analyze.
    if argv and argv[0] not in _SUBCOMMANDS and not argv[0].startswith("-"):
        argv = ["analyze", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    # `--top 0` means "all rows".
    if getattr(args, "top", None) == 0:
        args.top = None
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
