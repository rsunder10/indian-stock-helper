# indi-analyst: Codex project guidance

## Project shape

- Python 3.11+ package under `src/indi_analyst/`; tests are under `tests/`.
- Data flows through deterministic sources, indicators, `StockSnapshot`, levels / valuation / score, and finally an optional LLM narrative.
- The LLM layer explains computed results; it must not calculate or override them.
- Read `docs/architecture.md` for boundaries and `docs/methodology.md` before changing analytical formulas.

## Standard commands

```bash
uv sync
uv run --extra dev pytest
uv run --extra dev pytest tests/test_levels.py -q
uv run python -m compileall -q src tests
```

Tests must use fixtures or injected sources and must not depend on live market-data services.

## Non-negotiable quality rules

- Keep quantitative logic deterministic, pure where practical, auditable, and independently testable.
- Avoid look-ahead bias; a calculation may use only data available at or before the evaluated bar.
- Preserve unavailable values as unavailable. Never fabricate market data, fundamentals, news, or certainty.
- Keep data-source and LLM-provider seams protocol-based, injectable, lazily imported, and resilient to missing optional dependencies.
- Preserve rule-based fallback behavior and expose fallback, freshness, and data-quality warnings.
- Changes to weights, thresholds, formulas, or level invariants require regression tests plus `docs/methodology.md` updates.
- Changes to providers, environment variables, CLI behavior, or data coverage require the relevant docs update.
- Keep research/education disclaimers intact; do not turn output into personalized investment advice.
- Prefer the existing pandas/numpy/Pydantic stack over unnecessary dependencies.

## Handoff checklist

Inspect the final diff, add focused tests, run targeted tests and the complete offline suite, and state any checks that could not run. Do not commit `.env`, credentials, caches, generated artifacts, or machine-local settings.

## Project-local skills

Codex project skills are versioned under `.codex/skills/`. Read the relevant `SKILL.md` before specialized work; use only the smallest set needed:

- `.codex/skills/quantitative-analysis/SKILL.md` — indicators, valuation, scoring, levels, snapshots, and screener ranking.
- `.codex/skills/data-source-change/SKILL.md` — market data, news, caches, providers, environment, and external APIs.
- `.codex/skills/test-and-verify/SKILL.md` — targeted checks, full offline tests, and handoff evidence.
- `.codex/skills/documentation-alignment/SKILL.md` — keeping architecture, methodology, usage, provider, and roadmap docs current.

