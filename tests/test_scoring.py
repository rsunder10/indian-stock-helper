"""Scoring edge cases: settings injection and unavailable long-term direction."""

from __future__ import annotations

from indi_analyst.analysis.scoring import score
from indi_analyst.config import Settings
from indi_analyst.models import Fundamentals, SectorMacroSignal, StockSnapshot, TechnicalSignals


def test_adx_does_not_assume_missing_long_term_direction_is_down():
    snap = StockSnapshot(
        symbol="TEST.NS",
        query="TEST",
        fundamentals=Fundamentals(),
        technicals=TechnicalSignals(last_close=100.0, adx_14=35.0),
    )

    quant = score(snap, Settings(default_llm_provider="rulebased"))

    assert quant.technical_score == 50.0
    assert any("direction unavailable" in reason for reason in quant.reasons)
    assert not any("pointing down" in reason for reason in quant.reasons)


def test_score_uses_supplied_macro_cap():
    snap = StockSnapshot(
        symbol="TEST.NS",
        query="TEST",
        fundamentals=Fundamentals(),
        technicals=TechnicalSignals(last_close=100.0),
        macro_signals=[
            SectorMacroSignal(
                kind="budget",
                label="Budget",
                sector="Testing",
                tailwind=1.0,
            )
        ],
    )

    quant = score(snap, Settings(default_llm_provider="rulebased", macro_max_points=0.5))

    assert quant.macro_adjustment == 0.5
