# indi-analyst: Claude Code project guidance

## Project shape

- Python 3.11+ package under `src/indi_analyst/`; tests are under `tests/`.
- The deterministic pipeline is: data sources → indicators → `StockSnapshot` → trade levels / valuation / score → optional LLM narrative → `Recommendation`.
- The LLM layer writes prose from computed numbers. It must not be the source of numeric analysis.
- Read `docs/architecture.md` for module boundaries and `docs/methodology.md` before changing analytical formulas.

## Commands

```bash
uv sync --extra dev
uv run pytest
uv run pytest tests/test_levels.py -q
uv run python -m compileall -q src tests
```

Quality gates (also enforced in CI via `.github/workflows/ci.yml`; the same set runs on commit
if you `uv run pre-commit install`):

```bash
uv run ruff check .          # lint
uv run ruff format .         # auto-format (use --check in CI)
uv run mypy                  # static type check (src + scripts)
uv run pytest --cov          # tests with coverage (fails under 80%)
```

Use injected or fixture-backed sources for tests. Do not make live market-data calls in the test suite.

## Engineering rules

- Keep indicators, valuation, scoring, trade levels, and snapshot construction deterministic and independently testable.
- Never introduce look-ahead bias: calculations must use only data available at or before the current bar.
- Preserve missing data as missing. Do not invent fundamentals, prices, news, or confidence.
- Keep source and provider boundaries protocol-based and injectable. Optional SDKs must remain lazy imports.
- Preserve graceful fallback to rule-based analysis and surface fallback / freshness / data-quality warnings.
- Any change to weights, thresholds, formulas, or trade-level invariants requires regression tests and an update to `docs/methodology.md`.
- Any change to providers, environment variables, CLI behavior, or data coverage requires the relevant documentation update.
- Treat all outputs as research/education only; never present them as personalized investment advice.
- Avoid broad dependency additions when the existing pandas/numpy/Pydantic stack is sufficient.

## Definition of done

Inspect the diff, add or update focused tests, run the targeted tests and the full offline suite, and report any checks that could not run. Keep generated caches, `.env`, credentials, and machine-local files out of commits.

## Project skills

Use the smallest relevant project skill before starting specialized work:

- `/quantitative-analysis` for indicators, valuation, scoring, trade levels, snapshots, or screener ranking.
- `/data-source-change` for yfinance, RSS/news, caching, provider, environment, or external API changes.
- `/test-and-verify` before handoff or when reviewing a change.
- `/documentation-alignment` when behavior, methodology, CLI, configuration, provider, or roadmap docs change.

