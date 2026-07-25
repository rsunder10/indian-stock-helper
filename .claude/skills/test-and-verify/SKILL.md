---
name: test-and-verify
description: Verify Python changes in indi-analyst with focused tests, the complete offline suite, compile checks, and a clean handoff review. Use before finalizing implementation or reviewing a change.
---

# Test and verify

Use this skill before handoff, after a refactor, or when a review needs evidence rather than an intuition.

## Workflow

1. Inspect `git status` and the diff. Identify changed behavior and the tests that should protect it.
2. Run the narrowest relevant test first, for example:

   ```bash
   uv run --extra dev pytest tests/test_levels.py -q
   ```

3. Run the complete offline suite:

   ```bash
   uv run --extra dev pytest
   ```

4. Run a syntax/import sanity check when code changed broadly:

   ```bash
   uv run python -m compileall -q src tests
   ```

5. Review failures for real regressions; do not weaken assertions or skip tests merely to get green output. If a failure reflects an intentional contract change, update the implementation, tests, and docs together.
6. Check that tests remain network-free, optional provider imports remain optional, and no `.env`, credentials, caches, or generated artifacts were added.
7. Report commands and outcomes, including any check not run and why. Mention behavioral risks separately from tooling limitations.

## What good evidence covers

Prefer tests for invariants and edge cases over only happy-path examples: missing fundamentals, short histories, empty news, fallback providers, malformed external data, ordered trade targets, and deterministic repeatability.

