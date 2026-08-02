"""Fair-value math: blended methods, ratings, confidence, and graceful degradation."""

from __future__ import annotations

from math import isclose, sqrt

from indi_analyst.analysis.engine import analyze
from indi_analyst.analysis.valuation import compute_valuation, explain_valuation
from indi_analyst.config import Settings
from indi_analyst.indicators import technical
from indi_analyst.models import Conviction, Fundamentals, StockSnapshot
from tests.conftest import MockPriceSource, make_ohlcv


def _snap(fund: Fundamentals) -> StockSnapshot:
    df = make_ohlcv("up", n=300)
    return StockSnapshot(
        symbol="TEST.NS", query="TEST", technicals=technical.compute(df), fundamentals=fund
    )


def test_graham_and_earnings_blend():
    snap = _snap(Fundamentals(pe_ratio=20.0, pb_ratio=3.0, earnings_growth=0.16))
    price = snap.technicals.last_close
    val = compute_valuation(snap, Settings())

    eps, bvps = price / 20.0, price / 3.0
    graham = sqrt(22.5 * eps * bvps)
    earnings = 16.0 * eps  # fair P/E clamped to growth% (16)

    names = {m.name for m in val.methods}
    assert names == {"Graham number", "Earnings power"}
    assert val.confidence is Conviction.MEDIUM
    assert isclose(
        val.fair_value, round((round(graham, 2) + round(earnings, 2)) / 2, 2), abs_tol=0.05
    )
    assert isclose(val.low, round(min(graham, earnings), 2), abs_tol=0.05)
    assert isclose(val.high, round(max(graham, earnings), 2), abs_tol=0.05)
    # weights are normalized across the two active methods
    assert all(isclose(m.weight, 0.5) for m in val.methods)


def test_three_methods_give_high_confidence():
    snap = _snap(
        Fundamentals(pe_ratio=18.0, pb_ratio=2.5, earnings_growth=0.12, dividend_yield=0.02)
    )
    val = compute_valuation(snap, Settings())
    assert {m.name for m in val.methods} == {"Graham number", "Earnings power", "Dividend discount"}
    assert val.confidence is Conviction.HIGH


def test_dividend_growth_capped_at_terminal():
    # High earnings growth must be capped at terminal growth inside the Gordon model.
    snap = _snap(Fundamentals(dividend_yield=0.03, earnings_growth=0.40))
    price = snap.technicals.last_close
    settings = Settings()
    val = compute_valuation(snap, settings)

    d0 = 0.03 * price
    g = settings.fair_value_terminal_growth  # 0.40 capped to 0.05
    r = settings.fair_value_discount_rate
    expected = d0 * (1 + g) / (r - g)
    assert [m.name for m in val.methods] == ["Dividend discount"]
    assert val.confidence is Conviction.LOW
    assert isclose(val.fair_value, round(expected, 2), abs_tol=0.05)


def test_dividend_skipped_when_r_minus_g_unstable():
    # r - g < 0.02 -> the model is skipped; with no other data the valuation is empty.
    snap = _snap(Fundamentals(dividend_yield=0.03, earnings_growth=0.04))
    val = compute_valuation(
        snap, Settings(fair_value_discount_rate=0.06, fair_value_terminal_growth=0.05)
    )
    assert val.fair_value is None
    assert val.methods == []
    assert val.reasons  # explains why nothing could be computed


def test_no_fundamentals_degrades_gracefully():
    val = compute_valuation(_snap(Fundamentals()), Settings())
    assert val.fair_value is None
    assert val.rating is None
    assert val.confidence is None
    assert val.reasons  # a stated reason, never an exception


def test_rating_undervalued_and_threshold_honored():
    # Low P/E + high growth -> earnings power well above price -> undervalued.
    snap = _snap(Fundamentals(pe_ratio=10.0, earnings_growth=0.35))
    price = snap.technicals.last_close
    val = compute_valuation(snap, Settings())
    assert val.fair_value > price
    assert val.margin_of_safety > 0
    assert val.rating == "Undervalued"

    # A margin-of-safety threshold above the actual upside flips it to "Fairly valued".
    huge = compute_valuation(snap, Settings(margin_of_safety=5.0))
    assert huge.rating == "Fairly valued"


def test_reported_eps_and_book_value_preferred_over_ratios():
    # When the source reports EPS / book value directly, valuation uses those rather than
    # backing them out of P/E and P/B. Reported values differ from the ratio-implied ones,
    # so the Graham number lands on the reported inputs.
    snap = _snap(Fundamentals(pe_ratio=20.0, pb_ratio=3.0, eps=30.0, book_value=250.0))
    val = compute_valuation(snap, Settings())
    graham = next(m for m in val.methods if m.name == "Graham number")
    expected = round(sqrt(22.5 * 30.0 * 250.0), 2)
    assert isclose(graham.fair_value, expected, abs_tol=0.05)


def test_falls_back_to_ratio_derived_eps_when_unreported():
    # No reported EPS/book value -> derive from ratios exactly as before (regression guard).
    price = _snap(Fundamentals()).technicals.last_close
    snap = _snap(Fundamentals(pe_ratio=20.0, pb_ratio=3.0))
    val = compute_valuation(snap, Settings())
    graham = next(m for m in val.methods if m.name == "Graham number")
    expected = round(sqrt(22.5 * (price / 20.0) * (price / 3.0)), 2)
    assert isclose(graham.fair_value, expected, abs_tol=0.05)


def test_fair_pe_cap_honored():
    snap = _snap(Fundamentals(pe_ratio=15.0, earnings_growth=0.40))
    price = snap.technicals.last_close
    val = compute_valuation(snap, Settings(fair_pe_cap=20.0))
    eps = price / 15.0
    earnings = next(m for m in val.methods if m.name == "Earnings power")
    assert isclose(earnings.fair_value, round(20.0 * eps, 2), abs_tol=0.05)  # capped at 20, not 40


def test_explain_valuation_is_digestible():
    snap = _snap(
        Fundamentals(pe_ratio=18.0, pb_ratio=2.5, earnings_growth=0.12, dividend_yield=0.02)
    )
    val = compute_valuation(snap, Settings())
    exp = explain_valuation(val, snap.technicals.last_close, name="Test Co")

    assert exp is not None
    # a headline that names the price, the fair value, and a plain verdict word
    assert "Test Co" in exp.headline
    assert val.rating.lower() in exp.headline.lower()
    # one plain-English note per active method, each carrying its own ₹ estimate
    assert len(exp.method_notes) == len(val.methods)
    assert all("₹" in note for note in exp.method_notes)
    # margin + confidence get spelled out for the reader
    assert "Margin of safety" in exp.margin_note
    assert exp.confidence_note


def test_explain_valuation_none_when_no_fair_value():
    val = compute_valuation(_snap(Fundamentals()), Settings())  # no usable fundamentals
    assert explain_valuation(val, 100.0) is None


def test_end_to_end_recommendation_has_valuation():
    fund = Fundamentals(
        pe_ratio=20.0, pb_ratio=3.0, earnings_growth=0.16, dividend_yield=0.012, sector="Testing"
    )
    src = MockPriceSource(make_ohlcv("up"), fundamentals=fund)
    rec = analyze(
        "TEST",
        provider="rulebased",
        settings=Settings(default_llm_provider="rulebased"),
        price_source=src,
        news_source=None,
    )
    assert rec.valuation.fair_value is not None
    assert rec.valuation.rating in {"Undervalued", "Fairly valued", "Overvalued"}
    # the rule-based verdict surfaces the fair-value read in its thesis
    assert any("Fair value" in t for t in rec.verdict.thesis)
