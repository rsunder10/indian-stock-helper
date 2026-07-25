---
name: data-source-change
description: Add or change market-data, news, cache, provider, or external API integrations with explicit data-quality and failure handling. Use for yfinance, RSS, provider, config, environment, or network-bound changes.
---

# Data source change

Use this skill for `datasources/`, `llm/` provider adapters, caches, configuration, environment variables, and any network boundary.

## Workflow

1. Read `docs/architecture.md`, `docs/providers.md`, and the source protocols / factory before editing.
2. Keep integrations behind the existing protocols and factory seams. Make sources injectable so tests can run without network access.
3. Normalize and validate inputs at the boundary: symbol resolution, OHLCV columns, chronological ordering, timezone assumptions, numeric types, missing rows, stale data, empty responses, and malformed provider payloads.
4. Document every source or provider’s coverage, delay, quota, terms, required configuration, optional dependency, and failure behavior in `docs/providers.md` or the relevant docs.
5. Preserve lazy imports for optional SDKs, friendly configuration errors, rule-based fallback, and warnings that explain degraded data or provider behavior. Never log API keys or `.env` contents.
6. Make cache keys include the source and material query parameters. Respect configured TTLs and avoid returning stale data without a visible warning when freshness matters.
7. Add offline tests using fakes / fixtures for success, missing fields, empty data, malformed data, timeouts, provider unavailability, and fallback paths. Do not add live network calls to tests.
8. Update CLI, environment examples, architecture, and usage docs when the public configuration or behavior changes.

## Review focus

Check for silent data fabrication, import-time network calls, hard-coded credentials, provider-specific types leaking into core logic, accidental cache collisions, and fallback paths that hide a material data-quality issue.

