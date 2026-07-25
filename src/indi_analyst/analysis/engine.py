"""The top-level analysis entry point: query -> Recommendation."""

from __future__ import annotations

from indi_analyst.analysis.levels import compute_levels
from indi_analyst.analysis.scoring import score as quant_score
from indi_analyst.analysis.snapshot import build_snapshot
from indi_analyst.analysis.valuation import compute_valuation
from indi_analyst.config import Settings, get_settings
from indi_analyst.llm.base import LLMProvider, ProviderError
from indi_analyst.llm.factory import build_provider_with_fallback
from indi_analyst.llm.rulebased import RuleBasedProvider
from indi_analyst.models import Recommendation, StockSnapshot


def analyze(
    query: str,
    *,
    provider: str | None = None,
    settings: Settings | None = None,
    price_source=None,
    news_source=None,
) -> Recommendation:
    """Full pipeline. `provider` overrides the configured default; sources are injectable."""
    settings = settings or get_settings()

    snapshot = build_snapshot(
        query, settings=settings, price_source=price_source, news_source=news_source
    )
    return analyze_snapshot(snapshot, provider=provider, settings=settings)


def analyze_snapshot(
    snapshot: StockSnapshot,
    *,
    provider: str | None = None,
    settings: Settings | None = None,
) -> Recommendation:
    """Run levels + score + verdict over an already-built snapshot."""
    settings = settings or get_settings()

    levels = compute_levels(snapshot, settings)
    quant = quant_score(snapshot)
    valuation = compute_valuation(snapshot, settings)

    llm, note = build_provider_with_fallback(provider, settings)
    if note:
        snapshot.warnings.append(note)

    verdict = _run_verdict(llm, snapshot, levels, quant, valuation)

    # Resolve the final action: honor an analyst override only when it disagrees explicitly.
    final_action = quant.action
    if not verdict.action_agrees and verdict.suggested_action is not None:
        final_action = verdict.suggested_action

    return Recommendation(
        snapshot=snapshot,
        levels=levels,
        quant=quant,
        valuation=valuation,
        verdict=verdict,
        action=final_action,
        conviction=verdict.conviction,
        provider=getattr(llm, "name", "unknown"),
    )


def _run_verdict(llm: LLMProvider, snapshot, levels, quant, valuation):
    """Call the provider; if it fails at request time, degrade to rule-based."""
    try:
        return llm.verdict(snapshot, levels, quant, valuation)
    except ProviderError as e:
        if not isinstance(llm, RuleBasedProvider):
            snapshot.warnings.append(f"{getattr(llm, 'name', 'provider')} failed ({e}); used rule-based verdict.")
            return RuleBasedProvider().verdict(snapshot, levels, quant, valuation)
        raise
