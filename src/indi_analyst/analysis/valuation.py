"""Deterministic fair-value (intrinsic value) estimation.

Computed before any LLM sees the snapshot, so the tool always produces a concrete
"what is it worth?" number alongside the trade levels. A full DCF is impossible on free
yfinance data (no cash-flow statements), so we blend up to three cheap, transparent methods
built from the ratios we already have:

  1. Graham number     — √(22.5 · EPS · BVPS), a conservative value floor.
  2. Earnings power     — a growth-justified fair P/E × EPS (Peter Lynch / PEG≈1).
  3. Dividend discount  — Gordon growth model, for dividend payers only.

EPS and book value per share use the source's reported per-share figures when present, and
otherwise fall back to backing them out of the ratios (eps = price / pe, bvps = price / pb),
so nothing extra needs fetching. Every method that runs records an explainable `detail` line;
whichever run are equal-weighted into the blend.
Missing data degrades gracefully — an empty `Valuation` (with a reason), never an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    g = min(
        growth if growth is not None else settings.fair_value_terminal_growth,
        settings.fair_value_terminal_growth,
    )
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

    # Prefer the source's reported per-share figures; otherwise back them out of the ratios
    # (eps = price / pe, bvps = price / pb).
    eps = (
        f.eps
        if f.eps and f.eps > 0
        else (price / f.pe_ratio if f.pe_ratio and f.pe_ratio > 0 else None)
    )
    bvps = (
        f.book_value
        if f.book_value and f.book_value > 0
        else (price / f.pb_ratio if f.pb_ratio and f.pb_ratio > 0 else None)
    )
    growth = _growth(f)

    reasons: list[str] = []

    # The Gordon dividend-discount model assumes a durable payout. When we have corporate-action
    # history, only trust it for demonstrated consistent payers; skip it (with a stated reason)
    # for erratic/one-off payers. With no history available (ca is None), behaviour is unchanged.
    dd = _dividend_discount(price, f.dividend_yield, growth, settings)
    ca = snapshot.corporate_actions
    if dd is not None and ca is not None and ca.dividend_paying_years is not None:
        if ca.dividend_paying_years >= settings.dividend_min_consistent_years:
            dd.detail += f" · paid in {ca.dividend_paying_years} of last {ca.lookback_years} yrs"
        else:
            reasons.append(
                f"Dividend discount skipped — dividends in only {ca.dividend_paying_years} of the "
                f"last {ca.lookback_years} years, too inconsistent to model a durable payout."
            )
            dd = None

    candidates = [
        _graham(price, eps, bvps),
        _earnings_power(eps, growth, settings),
        dd,
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
        Conviction.HIGH
        if len(methods) >= 3
        else Conviction.MEDIUM
        if len(methods) == 2
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


# --- Plain-English explanation ------------------------------------------------
# Everything above is deterministic; the explanation below just translates those
# numbers into digestible prose for the "why this value?" detailed view. No LLM
# call is involved — the reasoning is the same math, said in words.

# One plain sentence per method: what it measures and why it's a sensible lens.
_METHOD_PLAIN: dict[str, str] = {
    "Graham number": (
        "a conservative 'floor' from earnings and book value — Benjamin Graham's "
        "classic defensive yardstick for what the business is worth on its assets."
    ),
    "Earnings power": (
        "what today's earnings are worth at a P/E multiple the company's growth "
        "can justify (a PEG≈1 rule of thumb)."
    ),
    "Dividend discount": (
        "the present value of its future dividend stream (Gordon growth model) — "
        "relevant because a dividend payer is worth the cash it will hand back."
    ),
}

_CONFIDENCE_PLAIN: dict[Conviction, str] = {
    Conviction.HIGH: (
        "All three methods could run on the available data and were blended, so this "
        "is a reasonably robust estimate."
    ),
    Conviction.MEDIUM: (
        "Two of the three methods could run on the available data — treat this as a "
        "solid but not definitive guide."
    ),
    Conviction.LOW: (
        "Only one method could run on the free data, so this is a rough, single-lens "
        "estimate — lean on it lightly."
    ),
}


@dataclass
class ValuationExplanation:
    """A digestible, plain-English account of how a fair value was reached."""

    headline: str  # one-line "undervalued/overvalued/fair" gist vs the price
    method_notes: list[str] = field(default_factory=list)  # one plain line per method
    blend_note: str = ""  # how the individual estimates combine into the number
    margin_note: str = ""  # what the margin of safety means for the investor
    confidence_note: str = ""  # how much trust the estimate warrants


def explain_valuation(
    val: Valuation, price: float, name: str | None = None
) -> ValuationExplanation | None:
    """Translate a computed `Valuation` into plain prose. Returns None if no fair value."""
    if val.fair_value is None:
        return None

    who = name or "the stock"
    fv = val.fair_value
    if val.rating == "Undervalued":
        headline = (
            f"At ₹{price:,.0f}, {who} trades **below** our fair-value estimate of "
            f"₹{fv:,.0f} — it looks **undervalued**, i.e. you may be paying less than "
            f"the fundamentals say it's worth."
        )
    elif val.rating == "Overvalued":
        headline = (
            f"At ₹{price:,.0f}, {who} trades **above** our fair-value estimate of "
            f"₹{fv:,.0f} — it looks **overvalued**, i.e. the price already runs ahead "
            f"of the fundamentals."
        )
    else:
        headline = (
            f"At ₹{price:,.0f}, {who} sits close to our fair-value estimate of "
            f"₹{fv:,.0f} — it looks **fairly priced** on the fundamentals."
        )

    method_notes = [
        f"{m.name} → ₹{m.fair_value:,.0f}: "
        f"{_METHOD_PLAIN.get(m.name, 'an intrinsic-value estimate.')}"
        for m in val.methods
    ]

    n = len(val.methods)
    if n > 1:
        blend_note = (
            f"Each method looks at value through a different lens; individually they "
            f"landed between ₹{val.low:,.0f} and ₹{val.high:,.0f}. The fair value ₹{fv:,.0f} "
            f"is their simple average, so no single lens dominates."
        )
    else:
        blend_note = (
            f"Only one lens could be applied, so the fair value ₹{fv:,.0f} is that single "
            f"method's estimate rather than a blend."
        )

    if val.margin_of_safety is not None:
        mos = val.margin_of_safety * 100
        if mos >= 0:
            margin_note = (
                f"Margin of safety is **+{mos:.0f}%** — the discount to fair value, a cushion "
                f"that absorbs estimate error before you'd overpay. Bigger is safer."
            )
        else:
            margin_note = (
                f"Margin of safety is **{mos:.0f}%** — the price is that much *above* fair value, "
                f"so there's no cushion; you'd be paying up for future growth to bail you out."
            )
    else:
        margin_note = ""

    confidence_note = _CONFIDENCE_PLAIN.get(val.confidence, "") if val.confidence else ""

    return ValuationExplanation(
        headline=headline,
        method_notes=method_notes,
        blend_note=blend_note,
        margin_note=margin_note,
        confidence_note=confidence_note,
    )
