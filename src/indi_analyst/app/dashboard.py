"""Streamlit dashboard: type a ticker -> charts + a recommendation card.

Run with:  uv run streamlit run src/indi_analyst/app/dashboard.py
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from indi_analyst.analysis.engine import analyze
from indi_analyst.analysis.valuation import explain_valuation
from indi_analyst.config import get_settings
from indi_analyst.datasources.factory import build_price_source
from indi_analyst.indicators import technical
from indi_analyst.models import Action, Recommendation
from indi_analyst.screener import scan_universe, shortlist_digest
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


def main() -> None:
    st.title("📈 indi-analyst")
    st.caption("Indian (NSE/BSE) stock analysis — buy / target / stop-loss with a facts-based thesis.")

    settings = get_settings()
    with st.sidebar:
        mode = st.radio("Mode", ["Single stock", "Screener"], horizontal=True)
        st.header(mode)
        providers = settings.configured_providers()
        default_idx = providers.index(settings.default_llm_provider) if settings.default_llm_provider in providers else 0
        provider = st.selectbox("LLM provider", providers, index=default_idx)
        st.caption("Price source: yfinance (free delayed/EOD baseline)")

    if mode == "Screener":
        _screener_view(settings, provider)
    else:
        _single_stock_view(settings, provider)

    st.sidebar.caption("Local Ollama & rule-based need no API key. Cloud providers read keys from .env.")


main()
