"""Core data models — the shared vocabulary between data, analysis, LLM, and UI layers.

The `StockSnapshot` is the single source of truth: everything a provider or the UI
needs to reason about a stock is captured here deterministically, before any LLM is involved.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Action(str, Enum):
    """The headline call. Ordered from most bullish to most bearish."""

    BUY = "BUY"
    ACCUMULATE = "ACCUMULATE"
    HOLD = "HOLD"
    AVOID = "AVOID"
    SELL = "SELL"


class Conviction(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Fundamentals(BaseModel):
    """Point-in-time fundamentals. All optional — free sources are patchy."""

    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    pb_ratio: float | None = None
    price_to_sales: float | None = None  # trailing P/S
    roe: float | None = None  # return on equity, fraction (0.18 == 18%)
    debt_to_equity: float | None = None
    profit_margin: float | None = None  # fraction
    revenue_growth: float | None = None  # yoy, fraction
    earnings_growth: float | None = None  # yoy, fraction
    dividend_yield: float | None = None  # fraction
    beta: float | None = None
    # Per-share absolutes (direct from the source when available; valuation prefers these over
    # backing them out of P/E and P/B ratios).
    eps: float | None = None  # trailing earnings per share, currency units
    book_value: float | None = None  # book value per share
    dividend_rate: float | None = None  # trailing annual dividend per share
    revenue_per_share: float | None = None
    next_earnings_date: datetime | None = None  # next scheduled results date, if known
    sector: str | None = None
    industry: str | None = None


class CorporateActions(BaseModel):
    """Free dividend + split history from the price source. All optional.

    Sources that don't expose corporate actions leave the whole object off the snapshot
    (`StockSnapshot.corporate_actions is None`), so downstream logic never assumes it exists.
    Facts are stored raw (counts, dates, ratios); interpretation (e.g. "consistent payer")
    is derived where it's used, against config thresholds.
    """

    dividend_paying_years: int | None = None  # distinct calendar years with a dividend, in the window
    lookback_years: int | None = None  # size of the window examined (for transparent detail)
    last_dividend: float | None = None  # most recent dividend per share, currency units
    last_dividend_date: date | None = None
    last_split_ratio: str | None = None  # e.g. "2:1" (bonus/split) or "1:5" (reverse)
    last_split_date: date | None = None
    recent_split: bool | None = None  # split within the recency window of the data as-of date


class SectorMacroSignal(BaseModel):
    """A sector-keyed macro overlay (budget, interest-rate cycle, …). Deterministic, from a pack.

    The general shape behind every government-open-data signal: resolved in `build_snapshot` by
    matching the stock's sector against a bundled, build-time-refreshed pack, and expressed as a
    normalized -1..+1 `tailwind` (a macro conviction signal, not a timing signal). The score turns
    the collected signals into a small, bounded, explainable nudge (see `analysis/macro.py`).
    Optional per source: an unmapped/None sector or a disabled/absent pack contributes nothing
    (missing stays missing).
    """

    kind: str  # source id, e.g. "budget" | "rate"
    label: str  # human-readable source label, e.g. "Union Budget 2026-27", "RBI rate cycle 2026-06"
    sector: str  # the sector key that matched (e.g. "Capital Goods")
    tailwind: float  # normalized -1..+1 (deterministic transform of the pack's numbers)
    drivers: list[str] = Field(default_factory=list)  # plain-English, e.g. "Defence capex +9.5% YoY"
    citations: list[str] = Field(default_factory=list)  # source URLs from the pack
    as_of: str | None = None  # period the pack covers, e.g. "2026-27" (budget year) or "2026-06"


class TechnicalSignals(BaseModel):
    """Computed indicators for the latest bar plus trend/level context."""

    last_close: float
    prev_close: float | None = None
    change_pct: float | None = None

    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None

    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ema_20: float | None = None

    bb_upper: float | None = None
    bb_lower: float | None = None
    bb_mid: float | None = None

    adx_14: float | None = None
    atr_14: float | None = None

    week52_high: float | None = None
    week52_low: float | None = None
    week52_position: float | None = None  # 0..1 where price sits in the 52w range

    volume: float | None = None
    avg_volume_20: float | None = None
    volume_ratio: float | None = None  # volume / avg_volume_20

    supports: list[float] = Field(default_factory=list)  # nearest below, ascending
    resistances: list[float] = Field(default_factory=list)  # nearest above, ascending

    trend: str | None = None  # e.g. "uptrend", "downtrend", "sideways"
    golden_cross: bool | None = None  # sma_50 > sma_200
    above_200sma: bool | None = None


class NewsItem(BaseModel):
    title: str
    link: str | None = None
    published: datetime | None = None
    source: str | None = None
    sentiment: float | None = None  # VADER compound, -1..1


class StockSnapshot(BaseModel):
    """Everything known about a stock at analysis time. Deterministic, LLM-free."""

    symbol: str  # resolved yfinance symbol, e.g. RELIANCE.NS
    query: str  # what the user typed
    name: str | None = None
    exchange: str | None = None  # NSE / BSE
    currency: str = "INR"
    as_of: datetime = Field(default_factory=_utcnow)  # when this snapshot was built
    data_source: str | None = None  # provider that supplied the price history, e.g. "yfinance"
    data_as_of: datetime | None = None  # timestamp of the latest price bar (data freshness)

    fundamentals: Fundamentals = Field(default_factory=Fundamentals)
    corporate_actions: CorporateActions | None = None  # dividend/split history, when the source has it
    macro_signals: list[SectorMacroSignal] = Field(default_factory=list)  # budget / rate / … sector overlays
    technicals: TechnicalSignals
    news: list[NewsItem] = Field(default_factory=list)
    news_sentiment: float | None = None  # mean VADER compound across headlines

    warnings: list[str] = Field(default_factory=list)  # data-quality notes

    @property
    def budget_signal(self) -> SectorMacroSignal | None:
        """The budget overlay, if any — a convenience accessor over `macro_signals`."""
        return next((m for m in self.macro_signals if m.kind == "budget"), None)


class TradeLevels(BaseModel):
    """Deterministic entry/target/stop, computed before any LLM sees the snapshot."""

    entry_low: float
    entry_high: float
    stop_loss: float
    stop_loss_pct: float  # negative fraction from entry midpoint
    target_1: float
    target_2: float
    target_1_pct: float  # positive fraction
    target_2_pct: float
    risk_reward: float  # (target_1 - entry) / (entry - stop)
    atr_multiple: float

    @property
    def entry_mid(self) -> float:
        return (self.entry_low + self.entry_high) / 2


class ValuationMethod(BaseModel):
    """One intrinsic-value estimate, kept explainable via its `detail` string."""

    name: str  # e.g. "Graham number", "Earnings power", "Dividend discount"
    fair_value: float
    weight: float  # contribution to the blend (normalized across active methods)
    detail: str  # human-readable how-it-was-derived line


class Valuation(BaseModel):
    """Deterministic intrinsic-value estimate, computed before any LLM sees the snapshot.

    Blends whichever free-data methods can run. All fields optional — free sources are
    patchy, so a stock with no usable fundamentals yields an empty Valuation (with a reason),
    never an error.
    """

    fair_value: float | None = None  # blended intrinsic value per share
    low: float | None = None  # most conservative method estimate
    high: float | None = None  # most optimistic method estimate
    margin_of_safety: float | None = None  # (fair - price)/price; positive == undervalued
    rating: str | None = None  # "Undervalued" | "Fairly valued" | "Overvalued"
    confidence: Conviction | None = None  # HIGH >=3 methods, MEDIUM 2, LOW 1
    methods: list[ValuationMethod] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)  # notes, incl. why a method was skipped


class QuantScore(BaseModel):
    """Composite deterministic score and its component breakdown."""

    action: Action
    conviction: Conviction
    score: float  # 0..100
    technical_score: float  # 0..100
    fundamental_score: float  # 0..100
    macro_adjustment: float = 0.0  # combined points the macro overlays added to the composite (bounded, signed)
    reasons: list[str] = Field(default_factory=list)  # bullet rationale


class AnalystVerdict(BaseModel):
    """The LLM's structured judgment over a snapshot + deterministic levels.

    Providers must return exactly this shape. In rule-based mode it's synthesized
    from the QuantScore so the rest of the pipeline is provider-agnostic.
    """

    thesis: list[str] = Field(default_factory=list, description="Why to invest — facts-based bullets")
    conviction: Conviction = Conviction.MEDIUM
    action_agrees: bool = True  # does the analyst agree with the quant action?
    suggested_action: Action | None = None  # override if it disagrees
    key_risks: list[str] = Field(default_factory=list)
    catalysts: list[str] = Field(default_factory=list)
    summary: str = ""  # one-paragraph plain-English gist


class Recommendation(BaseModel):
    """Final merged output rendered by the UI. Snapshot + levels + score + verdict."""

    snapshot: StockSnapshot
    levels: TradeLevels
    quant: QuantScore
    valuation: Valuation = Field(default_factory=Valuation)
    verdict: AnalystVerdict
    action: Action  # final resolved action
    conviction: Conviction
    provider: str  # which LLM/provider produced the verdict
    disclaimer: str = (
        "For research and educational purposes only. Not investment advice. "
        "Markets carry risk; verify independently before trading."
    )
