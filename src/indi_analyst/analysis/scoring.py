"""Composite quant score: technical + fundamental health -> action + conviction.

Transparent, rule-driven scoring so every point is explainable (the `reasons` list feeds
both the rule-based verdict and the UI). Scores are 0..100; 50 is neutral.
"""

from __future__ import annotations

from indi_analyst.analysis.macro import macro_score_delta
from indi_analyst.config import get_settings
from indi_analyst.models import Action, Conviction, QuantScore, StockSnapshot


def _technical_score(snap: StockSnapshot) -> tuple[float, list[str]]:
    t = snap.technicals
    score = 50.0
    reasons: list[str] = []

    # Trend / moving-average structure
    if t.above_200sma:
        score += 8
        reasons.append("Price above 200-day SMA (long-term uptrend intact).")
    elif t.above_200sma is False:
        score -= 8
        reasons.append("Price below 200-day SMA (long-term downtrend).")
    if t.golden_cross:
        score += 6
        reasons.append("50-SMA above 200-SMA (golden-cross structure).")
    elif t.golden_cross is False:
        score -= 6
        reasons.append("50-SMA below 200-SMA (death-cross structure).")

    # RSI
    if t.rsi_14 is not None:
        if t.rsi_14 < 30:
            score += 8
            reasons.append(f"RSI {t.rsi_14:.0f} — oversold, mean-reversion potential.")
        elif t.rsi_14 > 70:
            score -= 8
            reasons.append(f"RSI {t.rsi_14:.0f} — overbought, pullback risk.")
        elif 45 <= t.rsi_14 <= 60:
            score += 3
            reasons.append(f"RSI {t.rsi_14:.0f} — healthy momentum, not stretched.")

    # MACD
    if t.macd is not None and t.macd_signal is not None:
        if t.macd > t.macd_signal:
            score += 5
            reasons.append("MACD above its signal line (bullish momentum).")
        else:
            score -= 5
            reasons.append("MACD below its signal line (bearish momentum).")

    # ADX trend strength (only meaningful in combination with direction)
    if t.adx_14 is not None and t.adx_14 > 25:
        if t.above_200sma:
            score += 4
            reasons.append(f"ADX {t.adx_14:.0f} — strong, trending market (with the uptrend).")
        else:
            score -= 4
            reasons.append(f"ADX {t.adx_14:.0f} — strong trend, but pointing down.")

    # 52-week position
    if t.week52_position is not None:
        if t.week52_position > 0.9:
            score -= 3
            reasons.append("Near 52-week high — limited overhead room, extended.")
        elif t.week52_position < 0.2:
            score += 3
            reasons.append("Near 52-week low — value/mean-reversion zone (if fundamentals hold).")

    # Volume confirmation
    if t.volume_ratio is not None and t.volume_ratio > 1.5:
        reasons.append(f"Volume {t.volume_ratio:.1f}x its 20-day average — elevated participation.")

    # News sentiment nudge
    if snap.news_sentiment is not None:
        if snap.news_sentiment > 0.2:
            score += 3
            reasons.append(f"Positive news tone (sentiment {snap.news_sentiment:+.2f}).")
        elif snap.news_sentiment < -0.2:
            score -= 3
            reasons.append(f"Negative news tone (sentiment {snap.news_sentiment:+.2f}).")

    return max(0.0, min(100.0, score)), reasons


def _fundamental_score(snap: StockSnapshot) -> tuple[float, list[str]]:
    f = snap.fundamentals
    score = 50.0
    reasons: list[str] = []
    have_any = False

    if f.roe is not None:
        have_any = True
        if f.roe > 0.18:
            score += 8
            reasons.append(f"Strong ROE {f.roe * 100:.0f}% — efficient capital use.")
        elif f.roe < 0.08:
            score -= 6
            reasons.append(f"Weak ROE {f.roe * 100:.0f}%.")

    if f.debt_to_equity is not None:
        have_any = True
        # yfinance reports D/E as a percentage-like number (e.g. 45 == 0.45x)
        de = f.debt_to_equity / 100 if f.debt_to_equity > 5 else f.debt_to_equity
        if de < 0.5:
            score += 6
            reasons.append(f"Low leverage (D/E {de:.2f}).")
        elif de > 1.5:
            score -= 8
            reasons.append(f"High leverage (D/E {de:.2f}) — balance-sheet risk.")

    if f.revenue_growth is not None:
        have_any = True
        if f.revenue_growth > 0.15:
            score += 7
            reasons.append(f"Revenue growing {f.revenue_growth * 100:.0f}% YoY.")
        elif f.revenue_growth < 0:
            score -= 6
            reasons.append(f"Revenue shrinking {f.revenue_growth * 100:.0f}% YoY.")

    if f.profit_margin is not None:
        have_any = True
        if f.profit_margin > 0.15:
            score += 5
            reasons.append(f"Healthy net margin {f.profit_margin * 100:.0f}%.")
        elif f.profit_margin < 0:
            score -= 8
            reasons.append("Loss-making at the net level.")

    if f.pe_ratio is not None:
        have_any = True
        if 0 < f.pe_ratio < 15:
            score += 5
            reasons.append(f"Undemanding valuation (P/E {f.pe_ratio:.1f}).")
        elif f.pe_ratio > 60:
            score -= 5
            reasons.append(f"Rich valuation (P/E {f.pe_ratio:.0f}) — priced for growth.")

    if not have_any:
        reasons.append("Fundamentals unavailable from the free source — score is technical-only.")

    return max(0.0, min(100.0, score)), reasons


def score(snap: StockSnapshot) -> QuantScore:
    tech_score, tech_reasons = _technical_score(snap)
    fund_score, fund_reasons = _fundamental_score(snap)

    # Technicals drive timing (60%), fundamentals drive conviction (40%).
    composite = 0.6 * tech_score + 0.4 * fund_score

    # Macro overlays (budget, RBI rate cycle, …): a small, bounded, explainable nudge — combined
    # under a single cap (conviction tiebreaker, not a timing signal). Inert when there are no
    # signals: the backtest builds snapshots with empty fundamentals (no sector), so nothing fires.
    macro_reasons: list[str] = []
    macro_adjustment = 0.0
    if snap.macro_signals:
        macro_adjustment, macro_reasons = macro_score_delta(snap.macro_signals, get_settings())
        composite = max(0.0, min(100.0, composite + macro_adjustment))

    if composite >= 68:
        action = Action.BUY
    elif composite >= 57:
        action = Action.ACCUMULATE
    elif composite >= 45:
        action = Action.HOLD
    elif composite >= 35:
        action = Action.AVOID
    else:
        action = Action.SELL

    spread = abs(composite - 50)
    conviction = Conviction.HIGH if spread >= 18 else Conviction.MEDIUM if spread >= 8 else Conviction.LOW

    return QuantScore(
        action=action,
        conviction=conviction,
        score=round(composite, 1),
        technical_score=round(tech_score, 1),
        fundamental_score=round(fund_score, 1),
        macro_adjustment=macro_adjustment,
        reasons=tech_reasons + fund_reasons + macro_reasons,
    )
