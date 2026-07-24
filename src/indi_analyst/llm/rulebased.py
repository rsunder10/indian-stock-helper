"""Rule-based verdict — no LLM. Synthesizes an AnalystVerdict from the quant score.

Doubles as the universal fallback: if the chosen provider errors or is unreachable,
the pipeline degrades to this instead of failing.
"""

from __future__ import annotations

from indi_analyst.models import (
    Action,
    AnalystVerdict,
    QuantScore,
    StockSnapshot,
    TradeLevels,
)

_ACTION_GIST = {
    Action.BUY: "a compelling buy candidate",
    Action.ACCUMULATE: "worth accumulating on strength or dips",
    Action.HOLD: "best held / watched for now",
    Action.AVOID: "better avoided at current levels",
    Action.SELL: "a candidate to trim or exit",
}


class RuleBasedProvider:
    name = "rulebased"

    def verdict(
        self, snapshot: StockSnapshot, levels: TradeLevels, quant: QuantScore
    ) -> AnalystVerdict:
        t = snapshot.technicals
        f = snapshot.fundamentals

        # Thesis: promote the strongest scoring reasons to the top.
        thesis = list(quant.reasons[:5])

        risks: list[str] = []
        if t.rsi_14 is not None and t.rsi_14 > 70:
            risks.append("Overbought RSI — vulnerable to a near-term pullback.")
        if t.above_200sma is False:
            risks.append("Trading below the 200-day SMA — the primary trend is down.")
        if f.debt_to_equity is not None:
            de = f.debt_to_equity / 100 if f.debt_to_equity > 5 else f.debt_to_equity
            if de > 1.5:
                risks.append(f"Elevated leverage (D/E {de:.2f}) raises balance-sheet risk.")
        if not f.roe and not f.pe_ratio:
            risks.append("Sparse fundamentals from the free source — thesis leans technical.")
        if snapshot.news_sentiment is not None and snapshot.news_sentiment < -0.2:
            risks.append("Negative recent news flow.")
        if not risks:
            risks.append("Standard market and execution risk; levels can be invalidated by news.")

        catalysts: list[str] = []
        if t.resistances:
            catalysts.append(f"A close above ₹{t.resistances[0]:.0f} would confirm upside continuation.")
        if f.revenue_growth and f.revenue_growth > 0.15:
            catalysts.append("Sustained double-digit revenue growth into the next result.")
        catalysts.append("Quarterly results, sector rotation, and broad-market direction.")

        gist = _ACTION_GIST.get(quant.action, "under review")
        summary = (
            f"{snapshot.name or snapshot.symbol} scores {quant.score:.0f}/100 "
            f"(technical {quant.technical_score:.0f}, fundamental {quant.fundamental_score:.0f}) — {gist}. "
            f"Suggested entry ₹{levels.entry_low:.0f}–₹{levels.entry_high:.0f}, "
            f"stop ₹{levels.stop_loss:.0f} ({levels.stop_loss_pct * 100:+.1f}%), "
            f"targets ₹{levels.target_1:.0f} / ₹{levels.target_2:.0f}, "
            f"risk-reward {levels.risk_reward:.1f}:1."
        )

        return AnalystVerdict(
            thesis=thesis,
            conviction=quant.conviction,
            action_agrees=True,
            suggested_action=quant.action,
            key_risks=risks,
            catalysts=catalysts,
            summary=summary,
        )
