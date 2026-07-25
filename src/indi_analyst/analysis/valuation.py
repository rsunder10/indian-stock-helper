"""Deterministic fair-value (intrinsic value) estimation.

Computed before any LLM sees the snapshot, so the tool always produces a concrete
"what is it worth?" number alongside the trade levels. A full DCF is impossible on free
yfinance data (no cash-flow statements), so we blend up to three cheap, transparent methods
built from the ratios we already have:

  1. Graham number     — √(22.5 · EPS · BVPS), a conservative value floor.
  2. Earnings power     — a growth-justified fair P/E × EPS (Peter Lynch / PEG≈1).
  3. Dividend discount  — Gordon growth model, for dividend payers only.

EPS and book value per share are derived from the price and the P/E / P/B ratios
(eps = price / pe, bvps = price / pb), so nothing extra needs fetching. Every method that
runs records an explainable `detail` line; whichever run are equal-weighted into the blend.
Missing data degrades gracefully — an empty `Valuation` (with a reason), never an exception.
"""

from __future__ import annotations

from math import sqrt

from indi_analyst.config import Settings, get_settings
from indi_analyst.models import Conviction, StockSnapshot, Valuation, ValuationMethod

# A single method estimate outside this band (× current price) is treated as unreliable
# and dropped from the blend, so one wild number can't distort the fair value.
_SANITY_LOW = 0.1
_SANITY_HIGH = 10.0


def _growth(f) -> float | None:
    """Preferred growth input: earnings growth, falling back to revenue growth."""
    if f.earnings_growth is not None:
        return f.earnings_growth
    return f.revenue_growth


def _graham(price: float, eps: float | None, bvps: float | None) -> ValuationMethod | None:
    """Benjamin Graham's number — the classic defensive value floor."""
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    value = sqrt(22.5 * eps * bvps)
    return ValuationMethod(
        name="Graham number",
        fair_value=round(value, 2),
        weight=1.0,
        detail=f"√(22.5 × EPS ₹{eps:.1f} × BVPS ₹{bvps:.1f}) = ₹{value:,.0f}",
    )


def _earnings_power(
    eps: float | None, growth: float | None, settings: Settings
) -> ValuationMethod | None:
    """Growth-justified fair P/E × EPS (PEG≈1 heuristic, clamped to a sane multiple band)."""
    if eps is None or eps <= 0:
        return None
    if growth is not None:
        raw_pe = growth * 100  # 18% growth -> a fair P/E of ~18
        source = f"growth {growth * 100:.0f}%"
    else:
        raw_pe = settings.fair_pe_base
        source = f"base {settings.fair_pe_base:.0f}"
    fair_pe = max(settings.fair_pe_floor, min(settings.fair_pe_cap, raw_pe))
    value = fair_pe * eps
    return ValuationMethod(
        name="Earnings power",
        fair_value=round(value, 2),
        weight=1.0,
        detail=f"Fair P/E {fair_pe:.0f} ({source}) × EPS ₹{eps:.1f} = ₹{value:,.0f}",
    )


def _dividend_discount(
    price: float, div_yield: float | None, growth: float | None, settings: Settings
) -> ValuationMethod | None:
    """Gordon growth model — only meaningful for dividend payers with a stable r − g."""
    if not div_yield or div_yield <= 0:
        return None
    # yfinance usually reports the yield as a fraction (0.012); guard the odd percent form.
    dy = div_yield / 100 if div_yield > 1 else div_yield
    d0 = dy * price  # current annual dividend per share
    g = min(growth if growth is not None else settings.fair_value_terminal_growth,
            settings.fair_value_terminal_growth)
    g = max(g, 0.0)
    r = settings.fair_value_discount_rate
    if r - g < 0.02:  # denominator too small -> the model explodes; skip it
        return None
    value = d0 * (1 + g) / (r - g)
    return ValuationMethod(
        name="Dividend discount",
        fair_value=round(value, 2),
        weight=1.0,
        detail=(
            f"Gordon: D ₹{d0:.1f} × (1+{g * 100:.0f}%) / "
            f"({r * 100:.0f}% − {g * 100:.0f}%) = ₹{value:,.0f}"
        ),
    )


def compute_valuation(snapshot: StockSnapshot, settings: Settings | None = None) -> Valuation:
    settings = settings or get_settings()
    price = snapshot.technicals.last_close
    f = snapshot.fundamentals

    # Back out per-share earnings / book value from the ratios (eps = price / pe, etc.).
    eps = price / f.pe_ratio if f.pe_ratio and f.pe_ratio > 0 else None
    bvps = price / f.pb_ratio if f.pb_ratio and f.pb_ratio > 0 else None
    growth = _growth(f)

    reasons: list[str] = []
    candidates = [
        _graham(price, eps, bvps),
        _earnings_power(eps, growth, settings),
        _dividend_discount(price, f.dividend_yield, growth, settings),
    ]

    methods: list[ValuationMethod] = []
    for m in candidates:
        if m is None:
            continue
        if not (_SANITY_LOW * price <= m.fair_value <= _SANITY_HIGH * price):
            reasons.append(f"{m.name} (₹{m.fair_value:,.0f}) looks unreliable vs price — excluded.")
            continue
        methods.append(m)

    if not methods:
        reasons.append(
            "Fair value needs P/E, P/B, or dividend data unavailable from the free source."
        )
        return Valuation(reasons=reasons)

    # Equal-weight blend of the methods that survived.
    weight = round(1.0 / len(methods), 4)
    for m in methods:
        m.weight = weight
    values = [m.fair_value for m in methods]
    fair_value = round(sum(values) / len(values), 2)
    low = round(min(values), 2)
    high = round(max(values), 2)

    margin = (fair_value - price) / price if price else None
    if margin is None:
        rating = None
    elif margin >= settings.margin_of_safety:
        rating = "Undervalued"
    elif margin <= -settings.margin_of_safety:
        rating = "Overvalued"
    else:
        rating = "Fairly valued"

    confidence = (
        Conviction.HIGH if len(methods) >= 3
        else Conviction.MEDIUM if len(methods) == 2
        else Conviction.LOW
    )

    return Valuation(
        fair_value=fair_value,
        low=low,
        high=high,
        margin_of_safety=round(margin, 4) if margin is not None else None,
        rating=rating,
        confidence=confidence,
        methods=methods,
        reasons=reasons,
    )
