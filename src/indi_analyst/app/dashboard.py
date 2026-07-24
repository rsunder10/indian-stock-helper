"""Streamlit dashboard: type a ticker -> charts + a recommendation card.

Run with:  uv run streamlit run src/indi_analyst/app/dashboard.py
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from indi_analyst.analysis.engine import analyze
from indi_analyst.config import get_settings
from indi_analyst.datasources.yfinance_source import YFinanceSource
from indi_analyst.indicators import technical
from indi_analyst.models import Action, Recommendation

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
    return YFinanceSource().history(symbol, period=period)


@st.cache_data(show_spinner=False, ttl=900)
def _run(query: str, provider: str, period: str) -> Recommendation:
    settings = get_settings()
    settings.history_period = period
    return analyze(query, provider=provider, settings=settings)


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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entry zone", f"₹{lv.entry_low:,.0f}–{lv.entry_high:,.0f}")
    c2.metric("Stop loss", f"₹{lv.stop_loss:,.0f}", f"{lv.stop_loss_pct * 100:+.1f}%")
    c3.metric("Target 1 / 2", f"₹{lv.target_1:,.0f} / {lv.target_2:,.0f}",
              f"{lv.target_1_pct * 100:+.1f}% / {lv.target_2_pct * 100:+.1f}%")
    c4.metric("Risk : Reward", f"{lv.risk_reward:.2f} : 1")


def main() -> None:
    st.title("📈 indi-analyst")
    st.caption("Indian (NSE/BSE) stock analysis — buy / target / stop-loss with a facts-based thesis.")

    settings = get_settings()
    with st.sidebar:
        st.header("Analyze")
        query = st.text_input("Ticker / symbol", value="RELIANCE", help="e.g. RELIANCE, TCS, INFY.NS")
        providers = settings.configured_providers()
        default_idx = providers.index(settings.default_llm_provider) if settings.default_llm_provider in providers else 0
        provider = st.selectbox("LLM provider", providers, index=default_idx)
        period = st.selectbox("History", ["6mo", "1y", "2y", "5y"], index=1)
        go_btn = st.button("Analyze", type="primary", width="stretch")
        st.caption("Local Ollama & rule-based need no API key. Cloud providers read keys from .env.")

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

    with st.expander("Fundamentals"):
        f = s.fundamentals
        rows = {
            "Market cap": f.market_cap, "P/E": f.pe_ratio, "Forward P/E": f.forward_pe,
            "P/B": f.pb_ratio, "ROE": f.roe, "Debt/Equity": f.debt_to_equity,
            "Profit margin": f.profit_margin, "Revenue growth": f.revenue_growth,
            "Dividend yield": f.dividend_yield, "Sector": f.sector, "Industry": f.industry,
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


main()
