"""Minimal CLI for quick terminal analysis and smoke-testing the pipeline.

Usage:
    indi-analyst RELIANCE                     # single-stock deep dive (default)
    indi-analyst TCS --provider rulebased
    indi-analyst analyze INFY --provider ollama

    indi-analyst screen --universe nifty50 --provider rulebased --top 15
    indi-analyst screen --universe nifty50 --preset high-conviction-buys --digest

    indi-analyst backtest RELIANCE                     # walk-forward test of one symbol
    indi-analyst backtest --universe nifty50 --period 5y --top 20
"""

from __future__ import annotations

import argparse
import sys

from indi_analyst.analysis.engine import analyze
from indi_analyst.analysis.valuation import explain_valuation
from indi_analyst.backtest import run_backtest
from indi_analyst.backtest.models import BacktestResult, BacktestStats
from indi_analyst.config import get_settings
from indi_analyst.models import Action, Recommendation
from indi_analyst.screener import (
    resolve_preset,
    scan_universe,
    shortlist_digest,
    summarize_sectors,
)
from indi_analyst.screener.filters import apply, rank
from indi_analyst.screener.models import ScanResult, ScreenFilter


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _plain(text: str) -> str:
    """Strip the markdown emphasis markers used by the Streamlit view for a clean terminal line."""
    return text.replace("**", "")


def _fundamentals_line(f) -> str:
    """One compact line of the fundamentals that are present. Empty string if none are."""
    parts: list[str] = []
    if f.pe_ratio is not None:
        parts.append(f"P/E {f.pe_ratio:.1f}")
    if f.eps is not None:
        parts.append(f"EPS ₹{f.eps:,.1f}")
    if f.book_value is not None:
        parts.append(f"BVPS ₹{f.book_value:,.1f}")
    if f.roe is not None:
        parts.append(f"ROE {f.roe * 100:.0f}%")
    if f.dividend_yield is not None:
        parts.append(f"Div yld {f.dividend_yield * 100:.1f}%")
    if f.debt_to_equity is not None:
        parts.append(f"D/E {f.debt_to_equity:.2f}")
    return "   ".join(parts)


def _corporate_actions_lines(ca) -> list[str]:
    """Compact dividend/split history lines. Empty list when the source had no actions."""
    if ca is None:
        return []
    out: list[str] = []
    if ca.dividend_paying_years is not None and ca.lookback_years:
        line = f"Dividends: paid in {ca.dividend_paying_years} of last {ca.lookback_years} yrs"
        if ca.last_dividend is not None and ca.last_dividend_date is not None:
            line += f" · last ₹{ca.last_dividend:,.2f} on {ca.last_dividend_date:%Y-%m-%d}"
        out.append(line)
    if ca.last_split_ratio and ca.last_split_date is not None:
        tag = " (recent)" if ca.recent_split else ""
        out.append(f"Split: {ca.last_split_ratio} on {ca.last_split_date:%Y-%m-%d}{tag}")
    return out


def render(rec: Recommendation) -> str:
    s, t, lv, v, q = rec.snapshot, rec.snapshot.technicals, rec.levels, rec.verdict, rec.quant
    val = rec.valuation
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
    if s.data_source:
        as_of = f" · as of {s.data_as_of:%Y-%m-%d}" if s.data_as_of else ""
        lines.append(f"Data: {s.data_source}{as_of}")
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
    if val.fair_value is not None:
        lines.append("")
        lines.append("FAIR VALUE")
        conf = f" · {val.confidence.value} confidence" if val.confidence else ""
        lines.append(
            f"  Estimate : ₹{val.fair_value:,.2f}  (range ₹{val.low:,.0f}–₹{val.high:,.0f})"
        )
        if val.margin_of_safety is not None:
            lines.append(
                f"  Vs price : {_fmt_pct(val.margin_of_safety)} — {val.rating}{conf}"
            )
        exp = explain_valuation(val, t.last_close, s.name or s.symbol)
        if exp is not None:
            lines.append("")
            lines.append(f"  Why? {_plain(exp.headline)}")
            lines.append("  How we got there:")
            for note in exp.method_notes:
                lines.append(f"    · {_plain(note)}")
            if exp.blend_note:
                lines.append(f"  {_plain(exp.blend_note)}")
            if exp.margin_note:
                lines.append(f"  {_plain(exp.margin_note)}")
            if exp.confidence_note:
                lines.append(f"  ({exp.confidence_note})")
            lines.append("  The maths:")
        for m in val.methods:
            lines.append(f"    · {m.detail}")
    fund_line = _fundamentals_line(s.fundamentals)
    ca_lines = _corporate_actions_lines(s.corporate_actions)
    if fund_line or ca_lines:
        lines.append("")
        lines.append("FUNDAMENTALS")
        if fund_line:
            lines.append(f"  {fund_line}")
        if s.fundamentals.next_earnings_date is not None:
            lines.append(f"  Next results: {s.fundamentals.next_earnings_date:%Y-%m-%d}")
        for cl in ca_lines:
            lines.append(f"  {cl}")
    if s.macro_signals:
        lines.append("")
        adj = f"  (combined score {q.macro_adjustment:+.1f} pts)" if q.macro_adjustment else ""
        lines.append(f"MACRO OVERLAYS{adj}")
        for m in s.macro_signals:
            lines.append(f"  {m.label} · {m.sector} · tailwind {m.tailwind:+.2f}")
            for d in m.drivers:
                lines.append(f"    · {d}")
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
    lines.append("=" * 88)
    lines.append(
        f"SCREEN: {result.universe}   provider: {result.provider}   "
        f"scanned {result.ok_count} ok / {result.error_count} err   showing {len(shown)}"
    )
    lines.append("=" * 88)
    header = (
        f"{'#':>2}  {'SYMBOL':<14}{'ACTION':<11}{'CONV':<7}{'SCORE':>6}"
        f"{'CLOSE':>11}{'R:R':>6}{'UPSIDE':>8}  SECTOR"
    )
    lines.append(header)
    lines.append("-" * 88)
    for i, r in enumerate(shown, 1):
        lines.append(
            f"{i:>2}  {r.symbol:<14}"
            f"{(r.action.value if r.action else '—'):<11}"
            f"{(r.conviction.value if r.conviction else '—'):<7}"
            f"{(f'{r.score:.0f}' if r.score is not None else '—'):>6}"
            f"{(f'₹{r.last_close:,.0f}' if r.last_close is not None else '—'):>11}"
            f"{(f'{r.risk_reward:.1f}' if r.risk_reward is not None else '—'):>6}"
            f"{(_fmt_pct(r.margin_of_safety) if r.margin_of_safety is not None else '—'):>8}"
            f"  {(r.sector or '')[:26]}"
        )
    if not shown:
        lines.append("  (no rows matched the filter)")
    for w in result.warnings:
        lines.append(f"! {w}")
    return "\n".join(lines)


def render_sectors(summaries: list, top: int | None = None) -> str:
    """A top-down sector-tailwind table: which sectors have the government wind at their back."""
    shown = summaries[:top] if top else summaries
    lines: list[str] = []
    lines.append("=" * 88)
    lines.append("SECTOR TAILWINDS (Union Budget)   — rank sectors, then drill in with --sector")
    lines.append("=" * 88)
    lines.append(f"{'#':>2}  {'SECTOR':<28}{'TAILWIND':>9}{'AVG':>7}{'N':>4}  TOP / BUDGET DRIVER")
    lines.append("-" * 88)
    for i, s in enumerate(shown, 1):
        tw = f"{s.budget_tailwind:+.2f}" if s.budget_tailwind is not None else "—"
        avg = f"{s.avg_score:.0f}" if s.avg_score is not None else "—"
        tops = ", ".join(sym.replace(".NS", "") for sym in s.top_symbols[:3])
        driver = f"  ·  {s.drivers[0]}" if s.drivers else ""
        lines.append(f"{i:>2}  {s.sector[:28]:<28}{tw:>9}{avg:>7}{s.n_stocks:>4}  {tops}{driver}")
    if not shown:
        lines.append("  (no sectors to summarize)")
    return "\n".join(lines)


def _stat_pct(x: float | None) -> str:
    return _fmt_pct(x) if x is not None else "—"


def _stats_line(label: str, s: BacktestStats) -> str:
    """One aligned row summarizing a BacktestStats block."""
    return (
        f"  {label:<14}"
        f"{s.trades:>6} trades"
        f"{(f'{s.win_rate * 100:.0f}%' if s.win_rate is not None else '—'):>8} win"
        f"{(f'{s.expectancy_r:+.2f}R' if s.expectancy_r is not None else '—'):>9} exp"
        f"{_stat_pct(s.avg_return_pct):>9} avg"
        f"{(f'{s.profit_factor:.2f}' if s.profit_factor is not None else '—'):>7} pf"
        f"{_stat_pct(s.max_drawdown):>9} maxDD"
    )


def render_backtest(result: BacktestResult, top: int | None = None) -> str:
    """A compact report: honesty header, aggregate stats, slices, and per-symbol rows."""
    s = result.stats
    lines: list[str] = []
    lines.append("=" * 88)
    lines.append(
        f"BACKTEST: {result.target}   period {result.period}   "
        f"entry {'/'.join(a.value for a in result.entry_actions)}   "
        f"warmup {result.warmup_bars}b   max-hold {result.max_hold_bars}b"
    )
    lines.append(
        "technical-signal backtest (fundamentals excluded — point-in-time unavailable from the free source)"
    )
    lines.append("=" * 88)
    lines.append(
        f"Symbols: {result.ok_symbols}/{result.symbols} ok   "
        f"Benchmark (mean buy & hold): {_stat_pct(s.buy_hold_return)}"
    )
    lines.append("")
    lines.append("AGGREGATE")
    lines.append(_stats_line("all", s))
    if s.trades:
        lines.append(
            f"  detail        avg win {_stat_pct(s.avg_win_pct)}   "
            f"avg loss {_stat_pct(s.avg_loss_pct)}   "
            f"avg hold {s.avg_bars_held:.0f} bars"
        )
    if result.by_action:
        lines.append("")
        lines.append("BY ENTRY ACTION")
        for name, st in result.by_action.items():
            lines.append(_stats_line(name, st))
    if result.by_conviction:
        lines.append("")
        lines.append("BY CONVICTION")
        for name, st in result.by_conviction.items():
            lines.append(_stats_line(name, st))

    per = [r for r in result.per_symbol if r.error is None and r.trades]
    if per:
        per = sorted(per, key=lambda r: len(r.trades), reverse=True)
        shown = per[:top] if top else per
        lines.append("")
        lines.append("PER SYMBOL")
        lines.append(f"  {'SYMBOL':<14}{'TRADES':>7}{'WIN':>7}{'AVG':>9}{'BUY&HOLD':>11}")
        for r in shown:
            st = _stats_for_symbol(r.trades)
            lines.append(
                f"  {r.symbol:<14}{len(r.trades):>7}"
                f"{(f'{st.win_rate * 100:.0f}%' if st.win_rate is not None else '—'):>7}"
                f"{_stat_pct(st.avg_return_pct):>9}"
                f"{_stat_pct(r.buy_hold_return):>11}"
            )
    for w in result.warnings:
        lines.append(f"! {w}")
    if not s.trades:
        lines.append("  (no trades were generated — try a longer --period or a lower warmup)")
    return "\n".join(lines)


def _stats_for_symbol(trades):
    from indi_analyst.backtest.metrics import compute_stats

    return compute_stats(trades)


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
    if args.min_upside is not None:
        data["min_upside"] = args.min_upside
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

    # Top-down sector view is built from the full scanned universe, not the filtered subset.
    sectors = summarize_sectors(result.ok_rows()) if args.sectors_summary else []

    if args.format == "json":
        import json

        payload = {
            "universe": result.universe,
            "provider": result.provider,
            "scanned_at": result.scanned_at.isoformat(),
            "rows": [r.model_dump(mode="json") for r in (rows[: args.top] if args.top else rows)],
            "warnings": result.warnings,
        }
        if args.sectors_summary:
            payload["sectors"] = [s.model_dump(mode="json") for s in sectors]
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if args.sectors_summary:
        print(render_sectors(sectors))
        print("")
    print(render_scan(result, rows, args.top))
    if args.digest:
        digest_result = ScanResult(
            universe=result.universe, provider=result.provider, rows=rows, warnings=[]
        )
        print("\n" + shortlist_digest(digest_result, n=args.top or 5))
    return 0


def _cmd_backtest(args) -> int:
    settings = get_settings()
    if args.period:
        settings.backtest_history_period = args.period
    if args.hold:
        settings.backtest_max_hold_bars = args.hold

    target = args.query or args.universe
    if not target:
        print("Provide a symbol (e.g. RELIANCE) or --universe nifty50.", file=sys.stderr)
        return 2

    def _progress(done: int, total: int, symbol: str) -> None:
        print(f"\rBacktesting {done}/{total} … {symbol:<16}", end="", file=sys.stderr, flush=True)

    try:
        result = run_backtest(
            target,
            settings=settings,
            limit=args.limit,
            on_progress=None if args.format == "json" else _progress,
        )
    except Exception as e:
        print(f"Error backtesting '{target}': {e}", file=sys.stderr)
        return 1
    if args.format != "json":
        print("", file=sys.stderr)  # end the progress line

    if args.format == "json":
        import json

        print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
        return 0

    print(render_backtest(result, top=args.top))
    return 0


# Subcommands whose names are reserved; anything else is treated as an `analyze` query.
_SUBCOMMANDS = {"analyze", "screen", "backtest"}


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
    p_sc.add_argument("--min-upside", type=float, default=None,
                      help="Minimum margin of safety vs fair value, e.g. 0.15 for +15%%.")
    p_sc.add_argument("--action", default=None, help="Comma list, e.g. BUY,ACCUMULATE.")
    p_sc.add_argument("--sector", default=None, help="Comma list of sector substrings.")
    p_sc.add_argument("--digest", action="store_true", help="Append a top-ideas digest.")
    p_sc.add_argument("--sectors-summary", action="store_true",
                      help="Prepend a top-down Union-Budget sector-tailwind ranking.")
    p_sc.add_argument("--no-cache", action="store_true", help="Bypass the snapshot cache.")
    p_sc.add_argument("--format", choices=["table", "json"], default="table")
    p_sc.set_defaults(func=_cmd_screen, top=15)

    p_bt = sub.add_parser(
        "backtest",
        help="Walk-forward test the technical signal + trade levels over history.",
    )
    p_bt.add_argument("query", nargs="?", default=None,
                      help="Single symbol, e.g. RELIANCE. Omit and use --universe for a batch.")
    p_bt.add_argument("--universe", default=None,
                      help="nifty50 | nifty200 | nifty500 | watchlist:SYM1,SYM2 | file:/path.csv")
    p_bt.add_argument("--period", default=None, help="History period, e.g. 3y, 5y, max (default: config).")
    p_bt.add_argument("--hold", type=int, default=None, help="Max bars to hold a trade (default: config).")
    p_bt.add_argument("--limit", type=int, default=None, help="Backtest at most N symbols.")
    p_bt.add_argument("--top", type=int, default=20, help="Show only the top N per-symbol rows (0 = all).")
    p_bt.add_argument("--format", choices=["table", "json"], default="table")
    p_bt.set_defaults(func=_cmd_backtest, top=20)
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
