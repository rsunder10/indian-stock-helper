"""Streamlit dashboard: type a ticker -> charts + a recommendation card.

Run with:  uv run streamlit run src/indi_analyst/app/dashboard.py
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from indi_analyst.analysis.engine import analyze
from indi_analyst.analysis.macro import national_context
from indi_analyst.analysis.valuation import explain_valuation
from indi_analyst.backtest import run_backtest
from indi_analyst.backtest.models import BacktestResult, BacktestStats
from indi_analyst.config import get_settings
from indi_analyst.datasources.factory import build_price_source
from indi_analyst.indicators import technical
from indi_analyst.models import Action, Recommendation
from indi_analyst.screener import scan_universe, shortlist_digest, summarize_sectors
from indi_analyst.screener.filters import apply, rank
from indi_analyst.screener.models import PRESETS, ScanResult, ScreenFilter

st.set_page_config(page_title="indi-analyst", page_icon="📈", layout="wide")

_ACTION_COLOR = {
    Action.BUY: "#16a34a",
    Action.ACCUMULATE: "#65a30d",
    Action.HOLD: "#ca8a04",
    Action.AVOID: "#ea580c",
    Action.SELL: "#dc2626",
}


@st.cache_data(show_spinner=False, ttl=900)
def _history(symbol: str, period: str) -> pd.DataFrame:
    return build_price_source().history(symbol, period=period)


@st.cache_data(show_spinner=False, ttl=900)
def _run(query: str, provider: str, period: str) -> Recommendation:
    settings = get_settings()
    settings.history_period = period
    return analyze(query, provider=provider, settings=settings,
                   price_source=build_price_source(settings))


@st.cache_data(show_spinner=False, ttl=1800)
def _scan(universe: str, provider: str, limit: int | None) -> ScanResult:
    return scan_universe(universe, provider=provider, limit=limit,
                         price_source=build_price_source(), use_cache=True)


@st.cache_data(show_spinner=False, ttl=1800)
def _backtest(target: str, period: str, hold: int, limit: int) -> BacktestResult:
    settings = get_settings()
    settings.backtest_history_period = period
    settings.backtest_max_hold_bars = hold
    return run_backtest(target, settings=settings, limit=limit,
                        price_source=build_price_source(settings))


def _price_chart(df: pd.DataFrame) -> go.Figure:
    close = df["Close"]
    bb_u, bb_m, bb_l = technical.bollinger(close)
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=("Price + SMAs + Bollinger", "RSI (14)", "MACD"),
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=close,
            name="Price", increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
        ),
        row=1, col=1,
    )
    for span, color in ((20, "#2563eb"), (50, "#9333ea"), (200, "#f59e0b")):
        if len(close) >= span:
            fig.add_trace(
                go.Scatter(x=df.index, y=close.rolling(span).mean(), name=f"SMA{span}",
                           line=dict(width=1, color=color)),
                row=1, col=1,
            )
    fig.add_trace(go.Scatter(x=df.index, y=bb_u, name="BB upper",
                             line=dict(width=1, color="#94a3b8", dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=bb_l, name="BB lower",
                             line=dict(width=1, color="#94a3b8", dash="dot"),
                             fill="tonexty", fillcolor="rgba(148,163,184,0.08)"), row=1, col=1)

    rsi = technical.rsi(close)
    fig.add_trace(go.Scatter(x=df.index, y=rsi, name="RSI", line=dict(color="#0ea5e9")), row=2, col=1)
    fig.add_hline(y=70, line=dict(color="#dc2626", width=1, dash="dash"), row=2, col=1)
    fig.add_hline(y=30, line=dict(color="#16a34a", width=1, dash="dash"), row=2, col=1)

    macd_line, signal_line, hist = technical.macd(close)
    fig.add_trace(go.Bar(x=df.index, y=hist, name="MACD hist",
                         marker_color="rgba(100,116,139,0.5)"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=macd_line, name="MACD", line=dict(color="#2563eb")), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=signal_line, name="signal", line=dict(color="#f59e0b")), row=3, col=1)

    fig.update_layout(
        height=680, margin=dict(l=10, r=10, t=40, b=10),
        xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.02, x=0),
        template="plotly_white",
    )
    return fig


def _recommendation_card(rec: Recommendation) -> None:
    color = _ACTION_COLOR.get(rec.action, "#334155")
    st.markdown(
        f"""
        <div style="border-left:6px solid {color};padding:0.6rem 1rem;background:rgba(148,163,184,0.08);border-radius:8px;">
          <span style="font-size:1.6rem;font-weight:700;color:{color};">{rec.action.value}</span>
          <span style="font-size:1rem;color:#64748b;">&nbsp;·&nbsp;{rec.conviction.value} conviction
          &nbsp;·&nbsp;score {rec.quant.score:.0f}/100 &nbsp;·&nbsp;via {rec.provider}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    lv = rec.levels
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Entry zone", f"₹{lv.entry_low:,.0f}–{lv.entry_high:,.0f}")
    c2.metric("Stop loss", f"₹{lv.stop_loss:,.0f}", f"{lv.stop_loss_pct * 100:+.1f}%")
    c3.metric("Target 1 / 2", f"₹{lv.target_1:,.0f} / {lv.target_2:,.0f}",
              f"{lv.target_1_pct * 100:+.1f}% / {lv.target_2_pct * 100:+.1f}%")
    c4.metric("Risk : Reward", f"{lv.risk_reward:.2f} : 1")
    val = rec.valuation
    if val.fair_value is not None:
        delta = (
            f"{val.margin_of_safety * 100:+.1f}% · {val.rating}"
            if val.margin_of_safety is not None else val.rating
        )
        c5.metric("Fair value", f"₹{val.fair_value:,.0f}", delta, delta_color="normal")
    else:
        c5.metric("Fair value", "—")


def _deep_dive(rec: Recommendation, df: pd.DataFrame) -> None:
    """Render a full single-stock deep dive (shared by the analyze view and screener drill-down)."""
    s, t = rec.snapshot, rec.snapshot.technicals
    st.subheader(f"{s.name or s.symbol}  ·  {s.symbol} ({s.exchange})")
    top = st.columns(4)
    top[0].metric("Last close", f"₹{t.last_close:,.2f}",
                  f"{t.change_pct * 100:+.2f}%" if t.change_pct is not None else None)
    top[1].metric("RSI (14)", f"{t.rsi_14:.0f}" if t.rsi_14 is not None else "—")
    top[2].metric("Trend", (t.trend or "—").title())
    top[3].metric("52w position", f"{t.week52_position * 100:.0f}%" if t.week52_position is not None else "—")

    _recommendation_card(rec)

    if s.macro_signals:
        adj = f" · combined score {rec.quant.macro_adjustment:+.1f} pts" if rec.quant.macro_adjustment else ""
        st.markdown(f"**Macro overlays**{adj}")
        nat = national_context()
        if nat:
            st.caption("🏦 Macro backdrop  ·  " + "   ·   ".join(nat))
        for m in s.macro_signals:
            icon = "🟢" if m.tailwind > 0 else "🔴" if m.tailwind < 0 else "⚪"
            st.markdown(f"{icon} **{m.label}** — {m.sector} · tailwind {m.tailwind:+.2f}")
            if m.drivers:
                st.caption("  ·  ".join(m.drivers))

    left, right = st.columns([2, 1])
    with left:
        st.plotly_chart(_price_chart(df), width="stretch")
    with right:
        st.markdown("**Thesis**")
        for b in rec.verdict.thesis:
            st.markdown(f"- {b}")
        if rec.verdict.summary:
            st.markdown("**Gist**")
            st.write(rec.verdict.summary)

    r1, r2 = st.columns(2)
    with r1:
        st.markdown("**Key risks**")
        for r in rec.verdict.key_risks:
            st.markdown(f"- {r}")
    with r2:
        st.markdown("**Catalysts**")
        for c in rec.verdict.catalysts:
            st.markdown(f"- {c}")

    val = rec.valuation
    if val.fair_value is not None:
        conf = f" · {val.confidence.value} confidence" if val.confidence else ""
        with st.expander(f"Fair value — ₹{val.fair_value:,.0f} ({val.rating}{conf})"):
            exp = explain_valuation(val, t.last_close, s.name or s.symbol)
            if exp is not None:
                st.markdown(f"#### Why ₹{val.fair_value:,.0f}?")
                st.markdown(exp.headline)
                st.markdown("**How we got there**")
                for note in exp.method_notes:
                    st.markdown(f"- {note}")
                if exp.blend_note:
                    st.markdown(exp.blend_note)
                if exp.margin_note:
                    st.info(exp.margin_note)
                if exp.confidence_note:
                    st.caption(f"ℹ️ {exp.confidence_note}")
                st.markdown("**The maths**")
            st.caption(
                f"Range ₹{val.low:,.0f}–₹{val.high:,.0f}"
                + (f"  ·  margin of safety {val.margin_of_safety * 100:+.1f}%"
                   if val.margin_of_safety is not None else "")
            )
            st.table(
                {"Method": [m.name for m in val.methods],
                 "Fair value (₹)": [f"{m.fair_value:,.0f}" for m in val.methods],
                 "How": [m.detail for m in val.methods]}
            )
            for reason in val.reasons:
                st.caption(f"• {reason}")

    with st.expander("Fundamentals"):
        f = s.fundamentals
        rows = {
            "Market cap": f.market_cap, "P/E": f.pe_ratio, "Forward P/E": f.forward_pe,
            "P/B": f.pb_ratio, "P/S": f.price_to_sales, "EPS": f.eps,
            "Book value/sh": f.book_value, "ROE": f.roe, "Debt/Equity": f.debt_to_equity,
            "Profit margin": f.profit_margin, "Revenue growth": f.revenue_growth,
            "Dividend yield": f.dividend_yield, "Dividend/sh": f.dividend_rate,
            "Next results": (f.next_earnings_date.date().isoformat() if f.next_earnings_date else None),
            "Sector": f.sector, "Industry": f.industry,
        }
        ca = s.corporate_actions
        if ca is not None:
            if ca.dividend_paying_years is not None and ca.lookback_years:
                rows["Dividend history"] = f"paid in {ca.dividend_paying_years} of last {ca.lookback_years} yrs"
            if ca.last_split_ratio and ca.last_split_date is not None:
                tag = " (recent)" if ca.recent_split else ""
                rows["Last split"] = f"{ca.last_split_ratio} on {ca.last_split_date.isoformat()}{tag}"
        for m in s.macro_signals:
            rows[f"{m.label} tailwind"] = f"{m.tailwind:+.2f} ({m.sector})"
        st.table({k: [("—" if v is None else v)] for k, v in rows.items()})

    if s.news:
        with st.expander(f"Recent news ({len(s.news)})"):
            for n in s.news:
                tone = "🟢" if (n.sentiment or 0) > 0.1 else "🔴" if (n.sentiment or 0) < -0.1 else "⚪"
                link = f"[{n.title}]({n.link})" if n.link else n.title
                st.markdown(f"{tone} {link}  \n*{n.source or ''} {n.published.date() if n.published else ''}*")

    if s.warnings:
        for w in s.warnings:
            st.warning(w)
    st.caption(rec.disclaimer)


def _single_stock_view(settings, provider: str) -> None:
    with st.sidebar:
        query = st.text_input("Ticker / symbol", value="RELIANCE", help="e.g. RELIANCE, TCS, INFY.NS")
        period = st.selectbox("History", ["6mo", "1y", "2y", "5y"], index=1)
        go_btn = st.button("Analyze", type="primary", width="stretch")

    if not go_btn:
        st.info("Enter a ticker in the sidebar and press **Analyze**.")
        return

    try:
        with st.spinner(f"Analyzing {query} …"):
            rec = _run(query.strip(), provider, period)
            df = _history(rec.snapshot.symbol, period)
    except Exception as e:
        st.error(f"Could not analyze '{query}': {e}")
        return

    _deep_dive(rec, df)


def _screener_view(settings, provider: str) -> None:
    with st.sidebar:
        universe = st.selectbox("Universe", ["nifty50", "nifty200", "nifty500"], index=0)
        limit = st.slider("Max symbols to scan", 5, 100, 20, step=5,
                          help="Caps the scan for speed. Rule-based provider is fastest.")
        preset = st.selectbox("Preset", ["(none)", *PRESETS.keys()], index=0)
        min_score = st.slider("Min score", 0, 100, 0, step=5)
        min_upside = st.slider("Min upside (fair value)", -50, 50, -50, step=5,
                               help="Margin of safety vs fair value, %. -50 = no filter.")
        scan_btn = st.button("Run scan", type="primary", width="stretch")

    if scan_btn:
        with st.spinner(f"Scanning {universe} (≤{limit} names) via {provider} …"):
            st.session_state["scan"] = _scan(universe, provider, limit)

    result: ScanResult | None = st.session_state.get("scan")
    if result is None:
        st.info("Pick a universe and press **Run scan** to rank ideas across it.")
        return

    # Build the active filter from preset + min-score.
    flt = ScreenFilter(**PRESETS[preset].model_dump()) if preset != "(none)" else ScreenFilter()
    if min_score:
        flt = ScreenFilter(**{**flt.model_dump(), "min_score": float(min_score)})
    if min_upside > -50:
        flt = ScreenFilter(**{**flt.model_dump(), "min_upside": min_upside / 100})
    active = flt if flt.model_dump(exclude_none=True) else None
    rows = rank(apply(result.rows, active), by="score")

    st.subheader(f"{result.universe} — {len(rows)} ideas")
    st.caption(f"Scanned {result.ok_count} ok / {result.error_count} err · verdict via {result.provider}")
    for w in result.warnings:
        st.warning(w)

    # National macro strip: sector-independent government-data headlines (regime, inflation, activity).
    nat = national_context()
    if nat:
        st.caption("🏦 Macro backdrop  ·  " + "   ·   ".join(nat))

    # Top-down: which sectors have the government wind at their back? Built from the full scanned
    # universe (not the filtered subset), so it answers "which sector" before "which stock". The
    # tailwind here is the COMBINED mean across every overlay (budget, rate, IIP, GST, credit, …).
    sectors = summarize_sectors(result.ok_rows())
    if sectors:
        with st.expander("🏛️ Sector macro tailwinds (all government overlays)", expanded=True):
            st.caption("Rank sectors by combined macro tailwind, then filter to one to find accumulate candidates.")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Sector": s.sector,
                        "Macro tailwind": s.macro_tailwind,
                        "Overlays": len(s.overlays),
                        "Avg score": s.avg_score,
                        "Stocks": s.n_stocks,
                        "Top names": ", ".join(sym.replace(".NS", "") for sym in s.top_symbols),
                        "Top driver": s.drivers[0] if s.drivers else "—",
                    }
                    for s in sectors
                ]),
                width="stretch", hide_index=True,
            )

    if not rows:
        st.info("No rows matched the filter. Loosen the preset or min-score.")
        return

    table = [
        {
            "Symbol": r.symbol,
            "Action": r.action.value if r.action else "—",
            "Conv": r.conviction.value if r.conviction else "—",
            "Score": r.score,
            "Tech": r.technical_score,
            "Fund": r.fundamental_score,
            "Macro": r.macro_points,
            "Close": r.last_close,
            "Fair value": r.fair_value,
            "Upside": r.margin_of_safety,
            "R:R": r.risk_reward,
            "Sector": r.sector,
        }
        for r in rows
    ]
    st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)

    with st.expander("Top-ideas digest", expanded=True):
        digest_result = ScanResult(universe=result.universe, provider=result.provider, rows=rows)
        st.text(shortlist_digest(digest_result, n=min(5, len(rows))))

    st.markdown("---")
    symbols = [r.symbol for r in rows]
    pick = st.selectbox("Drill into a stock", symbols, index=0)
    if st.button(f"Deep dive {pick}"):
        try:
            with st.spinner(f"Analyzing {pick} …"):
                rec = _run(pick, provider, "1y")
                df = _history(rec.snapshot.symbol, "1y")
            _deep_dive(rec, df)
        except Exception as e:
            st.error(f"Could not analyze '{pick}': {e}")


def _bt_pct(x: float | None) -> str:
    return f"{x * 100:+.1f}%" if x is not None else "—"


def _bt_stats_row(name: str, s: BacktestStats) -> dict:
    """A BacktestStats block flattened into one display row."""
    return {
        "Group": name,
        "Trades": s.trades,
        "Win rate": f"{s.win_rate * 100:.0f}%" if s.win_rate is not None else "—",
        "Expectancy (R)": f"{s.expectancy_r:+.2f}" if s.expectancy_r is not None else "—",
        "Avg return": _bt_pct(s.avg_return_pct),
        "Profit factor": f"{s.profit_factor:.2f}" if s.profit_factor is not None else "—",
        "Max DD": _bt_pct(s.max_drawdown),
    }


def _backtest_view(settings) -> None:
    with st.sidebar:
        target = st.text_input(
            "Symbol or universe", value="RELIANCE",
            help="A ticker (RELIANCE), watchlist:TCS,INFY, file:/path.csv, or nifty50/200/500.",
        )
        period = st.selectbox("History", ["2y", "3y", "5y", "max"], index=2)
        hold = st.slider("Max hold (bars)", 5, 120, settings.backtest_max_hold_bars, step=5)
        limit = st.slider(
            "Max symbols (batch runs)", 1, 50, 10, step=1,
            help="Caps how many names a watchlist/index backtest fetches, so the run stays responsive. "
                 "Full-index runs are better on the CLI.",
        )
        go_btn = st.button("Run backtest", type="primary", width="stretch")

    st.caption(
        "Technical-signal backtest — fundamentals/news are excluded (not point-in-time available "
        "from the free source). It measures the timing signal, not the fundamental score."
    )

    if go_btn:
        try:
            with st.spinner(f"Backtesting {target} over {period} …"):
                st.session_state["backtest"] = _backtest(target.strip(), period, hold, limit)
        except Exception as e:
            st.error(f"Could not backtest '{target}': {e}")
            return

    result: BacktestResult | None = st.session_state.get("backtest")
    if result is None:
        st.info("Enter a symbol or universe and press **Run backtest**.")
        return

    s = result.stats
    st.subheader(
        f"{result.target} — {s.trades} trades across {result.ok_symbols}/{result.symbols} symbol(s)"
    )
    st.caption(
        f"period {result.period} · entry {'/'.join(a.value for a in result.entry_actions)} · "
        f"warmup {result.warmup_bars}b · max-hold {result.max_hold_bars}b"
    )

    cols = st.columns(6)
    cols[0].metric("Win rate", f"{s.win_rate * 100:.0f}%" if s.win_rate is not None else "—")
    cols[1].metric("Expectancy", f"{s.expectancy_r:+.2f}R" if s.expectancy_r is not None else "—")
    cols[2].metric("Profit factor", f"{s.profit_factor:.2f}" if s.profit_factor is not None else "—")
    cols[3].metric("Avg return / trade", _bt_pct(s.avg_return_pct))
    cols[4].metric("Max drawdown", _bt_pct(s.max_drawdown))
    cols[5].metric("Vs buy & hold", _bt_pct(s.buy_hold_return),
                   help="Mean per-symbol buy-and-hold return over the same window.")

    for w in result.warnings:
        st.warning(w)

    if not s.trades:
        st.info("No trades were generated — try a longer history or a different symbol/universe.")
        return

    if result.by_action:
        st.markdown("**By entry action**")
        st.dataframe(
            pd.DataFrame([_bt_stats_row(k, v) for k, v in result.by_action.items()]),
            width="stretch", hide_index=True,
        )
    if result.by_conviction:
        st.markdown("**By conviction**")
        st.dataframe(
            pd.DataFrame([_bt_stats_row(k, v) for k, v in result.by_conviction.items()]),
            width="stretch", hide_index=True,
        )

    per = [r for r in result.per_symbol if r.error is None and r.trades]
    if len(per) > 1:
        st.markdown("**Per symbol**")
        st.dataframe(
            pd.DataFrame([
                {"Symbol": r.symbol, "Trades": len(r.trades), "Buy & hold": _bt_pct(r.buy_hold_return)}
                for r in sorted(per, key=lambda r: len(r.trades), reverse=True)
            ]),
            width="stretch", hide_index=True,
        )

    with st.expander(f"All trades ({s.trades})"):
        st.dataframe(
            pd.DataFrame([
                {
                    "Symbol": t.symbol,
                    "Entry": t.entry_date.isoformat(),
                    "Exit": t.exit_date.isoformat(),
                    "Reason": t.exit_reason,
                    "Return": _bt_pct(t.return_pct),
                    "R": t.r_multiple,
                    "Bars": t.bars_held,
                    "Action": t.entry_action.value,
                    "Conv": t.entry_conviction.value,
                }
                for r in per for t in r.trades
            ]),
            width="stretch", hide_index=True,
        )

    st.caption(
        "Research/education only. Bar-resolution model — no intraday path, slippage, or costs; "
        "the shipped weights are not yet tuned to these results."
    )


def main() -> None:
    st.title("📈 indi-analyst")
    st.caption("Indian (NSE/BSE) stock analysis — buy / target / stop-loss with a facts-based thesis.")

    settings = get_settings()
    with st.sidebar:
        mode = st.radio("Mode", ["Single stock", "Screener", "Backtest"], horizontal=True)
        st.header(mode)
        provider = None
        if mode != "Backtest":  # the backtest is technical-only — no LLM verdict involved
            providers = settings.configured_providers()
            default_idx = providers.index(settings.default_llm_provider) if settings.default_llm_provider in providers else 0
            provider = st.selectbox("LLM provider", providers, index=default_idx)
        st.caption("Price source: yfinance (free delayed/EOD baseline)")

    if mode == "Screener":
        _screener_view(settings, provider)
    elif mode == "Backtest":
        _backtest_view(settings)
    else:
        _single_stock_view(settings, provider)

    st.sidebar.caption("Local Ollama & rule-based need no API key. Cloud providers read keys from .env.")


main()
