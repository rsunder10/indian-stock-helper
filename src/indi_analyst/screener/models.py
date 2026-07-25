"""Screener vocabulary: constituents, per-stock rows, scan results, and filters.

`ScreenRow` is a flat, rankable projection of a full `Recommendation` — everything a table
or filter needs, without carrying the whole snapshot around. It reuses the `Action`/`Conviction`
enums so filters and the UI speak the same language as the single-stock path.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from indi_analyst.models import Action, Conviction

# Bullish -> bearish, for min-conviction / action ordering comparisons.
_CONVICTION_RANK = {Conviction.LOW: 0, Conviction.MEDIUM: 1, Conviction.HIGH: 2}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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

    error: str | None = None  # set when this symbol failed to scan
    scanned_at: datetime = Field(default_factory=_utcnow)

    @property
    def ok(self) -> bool:
        return self.error is None


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
    trend: str | None = None  # e.g. "uptrend"

    def matches(self, row: ScreenRow) -> bool:
        if not row.ok:
            return False
        if self.actions and row.action not in self.actions:
            return False
        if self.min_conviction is not None:
            rc = _CONVICTION_RANK.get(row.conviction, -1)
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
        if self.trend is not None and (row.trend or "").lower() != self.trend.lower():
            return False
        return True


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
