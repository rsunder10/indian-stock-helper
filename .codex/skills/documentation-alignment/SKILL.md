---
name: documentation-alignment
description: Keep architecture, methodology, usage, provider, and roadmap documentation synchronized with implementation changes. Use when behavior, configuration, CLI, providers, analysis logic, or limitations change.
---

# Documentation alignment

Use this skill whenever a code change alters what users can do, what numbers mean, how data is obtained, or what limitations apply.

## Workflow

1. Identify the changed contract: formulas and assumptions → `docs/methodology.md`; module boundaries → `docs/architecture.md`; commands / library usage → `docs/usage.md`; providers and environment variables → `docs/providers.md`; planned work → `docs/roadmap.md`; high-level onboarding → `README.md`.
2. Read the relevant existing section before editing. Preserve terminology, examples, and warnings that are still accurate.
3. Describe current behavior, exact commands, data freshness, fallback behavior, and limitations. Do not claim live, predictive, or reliable behavior the code does not provide.
4. Keep examples executable and aligned with the public API. Update names, defaults, thresholds, and output fields whenever they change.
5. Check cross-links and remove stale examples rather than accumulating contradictory guidance.
6. If the change affects quantitative interpretation, pair this skill with the quantitative-analysis skill; if it affects external integrations, pair it with the data-source-change skill.

## Review focus

Look for docs that imply fabricated completeness, hide delayed / patchy data, omit fallback behavior, use obsolete CLI flags, or explain formulas differently from the implementation and tests.
