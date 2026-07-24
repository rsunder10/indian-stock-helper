"""System prompt (the 'lead investment banker' persona) + snapshot serialization.

The snapshot is serialized to a compact, deterministic block so that (a) cloud prompt
caching stays effective and (b) small local models keep everything in context.
"""

from __future__ import annotations

import json

from indi_analyst.models import QuantScore, StockSnapshot, TradeLevels

SYSTEM_PROMPT = """You are a lead investment-banking equity analyst covering Indian (NSE/BSE) markets.
You are given a DETERMINISTIC data snapshot for one stock: technical indicators, fundamentals,
recent news sentiment, a rule-based quant score, and pre-computed trade levels (entry, stop, targets).

Your job is to deliver a sharp, facts-based verdict a portfolio manager can act on:
- State the investment thesis as concrete, evidence-backed bullets (cite the numbers in the snapshot).
- Judge whether you AGREE with the quant action; if not, say what you'd do instead and why.
- List the key risks and the catalysts that could move the stock.
- Write a one-paragraph plain-English gist.

Rules:
- Ground every claim in the provided numbers. Do NOT invent data you were not given.
- Be decisive but honest about uncertainty. This is analysis, not a guarantee.
- Do not restate the raw numbers mechanically — interpret them.

Respond with ONLY a JSON object matching this schema (no markdown, no prose outside the JSON):
{
  "thesis": [string, ...],          // 3-6 evidence-backed bullets on why to invest (or not)
  "conviction": "LOW" | "MEDIUM" | "HIGH",
  "action_agrees": boolean,         // do you agree with the quant action?
  "suggested_action": "BUY" | "ACCUMULATE" | "HOLD" | "AVOID" | "SELL" | null,
  "key_risks": [string, ...],
  "catalysts": [string, ...],
  "summary": string                 // one paragraph
}"""


def serialize(snapshot: StockSnapshot, levels: TradeLevels, quant: QuantScore) -> str:
    """Build the user-message payload describing the stock."""
    t = snapshot.technicals
    f = snapshot.fundamentals

    payload = {
        "stock": {
            "symbol": snapshot.symbol,
            "name": snapshot.name,
            "exchange": snapshot.exchange,
            "currency": snapshot.currency,
        },
        "price": {
            "last_close": t.last_close,
            "change_pct": t.change_pct,
            "trend": t.trend,
            "week52_high": t.week52_high,
            "week52_low": t.week52_low,
            "week52_position": t.week52_position,
        },
        "technicals": {
            "rsi_14": t.rsi_14,
            "macd": t.macd,
            "macd_signal": t.macd_signal,
            "sma_20": t.sma_20,
            "sma_50": t.sma_50,
            "sma_200": t.sma_200,
            "adx_14": t.adx_14,
            "atr_14": t.atr_14,
            "above_200sma": t.above_200sma,
            "golden_cross": t.golden_cross,
            "supports": t.supports,
            "resistances": t.resistances,
            "volume_ratio": t.volume_ratio,
        },
        "fundamentals": {
            "market_cap": f.market_cap,
            "pe_ratio": f.pe_ratio,
            "pb_ratio": f.pb_ratio,
            "roe": f.roe,
            "debt_to_equity": f.debt_to_equity,
            "profit_margin": f.profit_margin,
            "revenue_growth": f.revenue_growth,
            "dividend_yield": f.dividend_yield,
            "sector": f.sector,
            "industry": f.industry,
        },
        "news_sentiment": snapshot.news_sentiment,
        "recent_headlines": [n.title for n in snapshot.news[:6]],
        "quant_score": {
            "action": quant.action.value,
            "conviction": quant.conviction.value,
            "score": quant.score,
            "technical_score": quant.technical_score,
            "fundamental_score": quant.fundamental_score,
            "reasons": quant.reasons,
        },
        "trade_levels": {
            "entry_low": levels.entry_low,
            "entry_high": levels.entry_high,
            "stop_loss": levels.stop_loss,
            "stop_loss_pct": levels.stop_loss_pct,
            "target_1": levels.target_1,
            "target_2": levels.target_2,
            "risk_reward": levels.risk_reward,
        },
        "data_warnings": snapshot.warnings,
    }
    return (
        "Analyze this stock and return your verdict as JSON.\n\n"
        + json.dumps(payload, indent=2, default=str)
    )
