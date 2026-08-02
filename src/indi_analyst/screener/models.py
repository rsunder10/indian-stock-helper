"""Screener vocabulary: constituents, per-stock rows, scan results, and filters.

`ScreenRow` is a flat, rankable projection of a full `Recommendation` — everything a table
or filter needs, without carrying the whole snapshot around. It reuses the `Action`/`Conviction`
enums so filters and the UI speak the same language as the single-stock path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from indi_analyst.models import Action, Conviction, MacroContribution, SectorMacroSignal

# Bullish -> bearish, for min-conviction / action ordering comparisons.
_CONVICTION_RANK = {Conviction.LOW: 0, Conviction.MEDIUM: 1, Conviction.HIGH: 2}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Constituent(BaseModel):
    """A member of a universe (index/watchlist), before any analysis."""

    symbol: str  # yfinance symbol, e.g. RELIANCE.NS
    name: str | None = None
    sector: str | None = None


class ScreenRow(BaseModel):
    """One scanned stock, flattened from a `Recommendation` for ranking/filtering."""

    symbol: str
    name: str | None = None
    sector: str | None = None

    action: Action | None = None
    conviction: Conviction | None = None
    score: float | None = None
    technical_score: float | None = None
    fundamental_score: float | None = None

    last_close: float | None = None
    change_pct: float | None = None
    pe_ratio: float | None = None

    fair_value: float | None = None
    margin_of_safety: float | None = None  # (fair - price)/price; positive == undervalued

    risk_reward: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    target_1: float | None = None

    trend: str | None = None
    thesis: list[str] = Field(default_factory=list)
    provider: str | None = None

    # Macro overlays (budget, rate, IIP, GST, credit, trade, input-cost, monsoon). `macro_points` is
    # the combined, bounded, signed nudge the overlays added to `score`; `macro_signals` carries every
    # overlay that fired so any screen can show per-source detail. `budget_tailwind`/`budget_drivers`
    # are kept as a back-compatible convenience over the budget overlay specifically.
    macro_points: float | None = (
        None  # combined macro nudge in score points (== quant.macro_adjustment)
    )
    macro_tailwind: float | None = None  # mean normalized tailwind across fired overlays
    macro_signal_count: int = 0  # number of government overlays that fired for this stock
    macro_refreshed_count: int = 0  # fired overlays with a non-null pack refresh timestamp
    macro_seed_count: int = 0  # fired overlays still carrying bundled seed data
    macro_signals: list[SectorMacroSignal] = Field(default_factory=list)  # every overlay that fired
    macro_breakdown: list[MacroContribution] = Field(
        default_factory=list
    )  # per-source points + raw evidence for JSON/UI consumers
    budget_tailwind: float | None = None  # sector budget tailwind (-1..+1), None if unmapped
    budget_drivers: list[str] = Field(default_factory=list)  # plain-English budget drivers

    error: str | None = None  # set when this symbol failed to scan
    scanned_at: datetime = Field(default_factory=_utcnow)

    @property
    def ok(self) -> bool:
        return self.error is None


class SectorSummary(BaseModel):
    """Top-down sector view: the combined macro tailwind plus how the sector's stocks scored.

    Answers "which sector" before "which stock" — aggregated from the scanned `ScreenRow`s so the
    per-sector macro overlays (a constant across the sector's stocks) sit alongside the bottom-up
    average score. `macro_tailwind` is the mean of every overlay's tailwind for the sector;
    `budget_tailwind` is kept as a back-compatible single-source view.
    """

    sector: str
    macro_tailwind: float | None = (
        None  # mean overlay tailwind across all sources, None if unmapped
    )
    overlays: list[str] = Field(
        default_factory=list
    )  # labels of the overlays that fired for the sector
    budget_tailwind: float | None = (
        None  # per-sector constant from the budget pack, None if unmapped
    )
    n_stocks: int = 0
    avg_score: float | None = None
    top_symbols: list[str] = Field(default_factory=list)  # highest-scoring names in the sector
    drivers: list[str] = Field(
        default_factory=list
    )  # one plain-English driver per overlay that fired
    overlay_tailwinds: dict[str, float] = Field(
        default_factory=dict
    )  # kind -> normalized tailwind, for heatmaps and exports
    overlay_points: dict[str, float] = Field(
        default_factory=dict
    )  # kind -> uncapped per-source score points
    refreshed_overlays: int = 0
    seed_overlays: int = 0


class ScanResult(BaseModel):
    """The outcome of scanning a universe: rows (ranked by score) + a little metadata."""

    universe: str
    provider: str
    scanned_at: datetime = Field(default_factory=_utcnow)
    rows: list[ScreenRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.rows if r.ok)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.rows if not r.ok)

    def ok_rows(self) -> list[ScreenRow]:
        return [r for r in self.rows if r.ok]

    def top(self, n: int, by: str = "score") -> list[ScreenRow]:
        from indi_analyst.screener.filters import rank

        return rank(self.ok_rows(), by=by)[:n]


class ScreenFilter(BaseModel):
    """Declarative screen. Every field is optional; `matches` ANDs the active ones."""

    actions: set[Action] | None = None
    min_conviction: Conviction | None = None
    sectors: set[str] | None = None  # case-insensitive substring match
    min_score: float | None = None
    max_pe: float | None = None
    min_rr: float | None = None
    min_upside: float | None = None  # min margin of safety vs fair value (fraction)
    min_macro_points: float | None = None  # min combined macro nudge in score points (e.g. 0.5)
    min_macro_tailwind: float | None = None  # min mean normalized government tailwind (-1..+1)
    min_macro_coverage: int | None = None  # minimum number of mapped government indicators
    require_refreshed_macro: bool = False  # exclude rows with any seed/unrefreshed overlay
    trend: str | None = None  # e.g. "uptrend"

    def matches(self, row: ScreenRow) -> bool:
        if not row.ok:
            return False
        if self.actions and row.action not in self.actions:
            return False
        if self.min_conviction is not None:
            rc = _CONVICTION_RANK.get(row.conviction, -1) if row.conviction is not None else -1
            if rc < _CONVICTION_RANK[self.min_conviction]:
                return False
        if self.sectors:
            sec = (row.sector or "").lower()
            if not any(s.lower() in sec for s in self.sectors):
                return False
        if self.min_score is not None and (row.score is None or row.score < self.min_score):
            return False
        if self.max_pe is not None:
            # No P/E (missing fundamentals) fails a valuation ceiling.
            if row.pe_ratio is None or not (0 < row.pe_ratio <= self.max_pe):
                return False
        if self.min_rr is not None and (row.risk_reward is None or row.risk_reward < self.min_rr):
            return False
        if self.min_upside is not None:
            # No fair value (missing fundamentals) fails an upside floor.
            if row.margin_of_safety is None or row.margin_of_safety < self.min_upside:
                return False
        if self.min_macro_points is not None:
            # No macro overlays (e.g. unmapped/sectorless) fails a macro-tailwind floor.
            if row.macro_points is None or row.macro_points < self.min_macro_points:
                return False
        if self.min_macro_tailwind is not None:
            if row.macro_tailwind is None or row.macro_tailwind < self.min_macro_tailwind:
                return False
        if self.min_macro_coverage is not None and row.macro_signal_count < self.min_macro_coverage:
            return False
        if self.require_refreshed_macro and (
            row.macro_signal_count == 0 or row.macro_seed_count > 0
        ):
            return False
        return not (self.trend is not None and (row.trend or "").lower() != self.trend.lower())


# Named screens — quick-start presets referenced by the CLI and dashboard.
PRESETS: dict[str, ScreenFilter] = {
    "high-conviction-buys": ScreenFilter(
        actions={Action.BUY, Action.ACCUMULATE},
        min_conviction=Conviction.HIGH,
        min_score=60,
    ),
    "oversold-quality": ScreenFilter(
        min_score=52,
        max_pe=35,
        min_rr=2.0,
    ),
    "breakout-with-fundamentals": ScreenFilter(
        actions={Action.BUY, Action.ACCUMULATE},
        trend="uptrend",
        min_rr=2.0,
    ),
}
