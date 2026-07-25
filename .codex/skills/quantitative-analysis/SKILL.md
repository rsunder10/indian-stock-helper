---
name: quantitative-analysis
description: Review or change deterministic indicators, valuation, scoring, trade levels, snapshots, or screener ranking safely. Use when a task can change numeric outputs, financial decision logic, or time-series behavior.
---

# Quantitative analysis

Use this skill for work in `indicators/`, `analysis/`, `models.py`, relevant screener logic, and tests that protect numeric behavior.

## Workflow

1. Read `docs/methodology.md`, `docs/architecture.md`, the affected models, and nearby tests before editing.
2. Trace raw inputs to derived outputs. Keep calculations deterministic and separate from LLM prompts or provider calls.
3. Protect time-series correctness: sort data chronologically, use only current and historical bars, handle warm-up `NaN`s and zero denominators explicitly, and preserve unavailable values as `None` rather than guessing.
4. Preserve analytical contracts:
   - trade levels keep a valid entry, stop below entry, positive risk, ordered targets, and positive risk-reward;
   - valuation methods contribute only when their required inputs are valid and retain an explainable detail;
   - score adjustments remain bounded, have human-readable reasons, and keep action / conviction thresholds explicit.
5. Add regression tests for the changed behavior and edge cases such as short histories, missing fundamentals, flat prices, zero volume, and empty news.
6. If a formula, threshold, weight, or output meaning changes, update `docs/methodology.md` and any affected usage or architecture examples.
7. Run the focused tests, then the full offline suite using the test-and-verify skill.

## Review focus

Look specifically for look-ahead bias, silently filled data, changed units, unstable ordering, accidental rounding, target collapse after resistance snapping, and prose that contradicts the deterministic fields.
