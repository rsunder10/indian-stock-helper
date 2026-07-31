# Architecture

`indi-analyst` is built around one principle: **the analysis is deterministic; the LLM is
optional.** Everything a decision needs — data, indicators, entry/target/stop levels, a
transparent score — is computed in Python and captured in a `StockSnapshot` *before* any model
is involved. A thin, swappable LLM layer then turns that snapshot into a written verdict. If no
model is available, a rule-based provider produces the verdict instead, and the numbers are
identical either way.

This keeps the tool:

- **Portable** across any LLM — cloud or a small local model — because the model only has to
  reason over a compact JSON snapshot, not orchestrate tools.
- **Resilient** — it degrades gracefully to rule-based analysis and never hard-fails on a
  missing key or an unreachable server.
- **Explainable** — every score point and level has a reason you can read.

---

## Data flow

```
          ┌─────────────────────────────────────────────────────────────┐
 query →  │  datasources/          indicators/         analysis/         │
          │  ─────────────         ────────────        ─────────         │
          │  YFinanceSource   →    technical.compute → snapshot.build →  │  StockSnapshot
          │  GoogleNewsSource                                            │  (deterministic)
          └───────────────────────────────┬─────────────────────────────┘
                                           │
                        ┌──────────────────┴───────────────────┐
                        ▼                                       ▼
                 analysis/levels.py                     analysis/scoring.py
                 (entry / stop / targets)               (technical + fundamental → action)
                        │                                       │
                        └──────────────────┬────────────────────┘
                                           ▼
                                    llm/factory.py
                                 build_provider_with_fallback
                                           ▼
                            LLMProvider.verdict(snapshot, levels, quant)
                           (ollama | anthropic | openai | gemini | rulebased)
                                           ▼
                                 analysis/engine.py
                             merge → Recommendation → UI / CLI
```

---

## Layers

### 1. Data sources — `src/indi_analyst/datasources/`

Free sources, behind protocols so they're swappable and testable.

- **`yfinance_source.py`** — resolves a query to an NSE/BSE symbol (bare `RELIANCE` → tries
  `RELIANCE.NS` then `.BO`), fetches OHLCV history and fundamentals from Yahoo Finance. Every
  network call is paced by a `RateLimiter` and wrapped in `retry` (transient blips get backed-off
  retries). `history()` **validates at the boundary** — re-sorts non-chronological bars, drops rows
  with missing OHLC / non-positive prices / inconsistent High-Low — and rides the results out on
  `df.attrs`: `source`, `as_of` (latest bar timestamp), and `warnings` (what was dropped/repaired).
- **`throttle.py`** — dependency-free `RateLimiter` (thread-safe minimum-interval gate; one shared
  instance paces a screener scan's worker threads as a group) and a `retry(fn, retries, backoff)`
  helper. No `tenacity`/`backoff` dependency.
- **`news.py`** — Google News RSS headlines (multi-query, deduped, freshest-first), scored with
  VADER sentiment. `aggregate_sentiment()` produces the recency-weighted mean used by scoring.
- **`base.py`** — `PriceSource`, `FundamentalsSource`, `NewsSource` `Protocol`s. Depending on
  the *shape* (not the concrete class) is what lets tests inject a mock source with zero network,
  and lets future free sources drop in without touching the engine. `history()` returns a bare
  `pd.DataFrame`; data-quality metadata travels on `df.attrs` so the protocol stays unchanged.
- **`factory.py`** — `build_price_source(settings)` constructs the supported free source, wiring in
  the rate limiter and retry knobs from `Settings`. The source boundary remains intentionally
  swappable for future free/local adapters, while the yfinance path is the supported baseline.

### 2. Indicators — `src/indi_analyst/indicators/technical.py`

The core indicator set (RSI, MACD, SMA/EMA 20/50/200, Bollinger, ADX, ATR, 52-week position,
volume ratio, swing support/resistance, trend classification) computed directly with
**pandas/numpy**. No `pandas-ta` dependency — it avoids that library's numpy/pandas version
breakage on Python 3.13 and keeps the math auditable. See
[methodology.md](methodology.md) for definitions.

### 3. Analysis — `src/indi_analyst/analysis/`

- **`snapshot.py`** — orchestrates fetch + compute into a `StockSnapshot` (sources are
  injectable for testing). Reads the price source's `df.attrs` right after fetch to merge
  data-quality warnings and record `data_source` / `data_as_of` provenance on the snapshot.
- **`levels.py`** — deterministic **entry zone**, **ATR-based stop-loss** (widened to structural
  support, never tightened below the ATR floor), and **risk-reward targets** snapped to distinct
  resistances.
- **`scoring.py`** — a transparent composite: **60% technical + 40% fundamental** → an `Action`
  (BUY/ACCUMULATE/HOLD/AVOID/SELL) and a conviction, with a `reasons` list.
- **`valuation.py`** — deterministic **fair value (intrinsic value)**: blends whichever of the
  Graham number, growth-justified fair-P/E (earnings power), and Gordon dividend-discount methods
  the free data supports into a single estimate, a low/high range, a margin of safety, and an
  under/fairly/over-valued rating. Each method keeps an explainable `detail`; missing data degrades
  to an empty `Valuation` rather than an error. `explain_valuation(val, price, name)` translates a
  computed `Valuation` into plain-English prose (headline verdict, what each method measures, how
  they blend, what the margin of safety means) — pure string formatting over already-computed
  numbers, no LLM involved, so it's instant and can never contradict the figures it explains.
- **`macro.py`** — the **macro-overlay framework**: runs every registered sector-keyed
  government-open-data resolver over a stock's sector and combines their point deltas under one shared
  cap (`resolve_macro_signals` → `list[SectorMacroSignal]`; `macro_score_delta` → the combined capped
  delta `scoring.py` applies). Adding a new dataset = a resolver + pack, registered here.
  - **`budget.py`** — Union-Budget allocation overlay (pack `data/budget_<year>.json`).
  - **`rates.py`** — RBI rate-cycle overlay (pack `data/rates_<version>.json`; easing/tightening ×
    a maintained sector rate-sensitivity crosswalk).
  - **`sector_match.py`** — shared case-insensitive exact-then-substring sector matcher (both NSE and
    yfinance taxonomies). All packs are network-free at runtime, refreshed at build time by
    `scripts/refresh_budget.py` / `scripts/refresh_rates.py`. Missing/unmapped sector → no signal.
- **`engine.py`** — the top-level entry point: `analyze(query, provider=...)` runs the whole
  pipeline and returns a `Recommendation`. Also `analyze_snapshot(...)` if you already have one.

### 4. LLM layer — `src/indi_analyst/llm/`

- **`base.py`** — the whole contract is one method:
  `verdict(snapshot, levels, quant, valuation) -> AnalystVerdict`.
- **`prompts.py`** — the "lead investment-banker" system prompt + a deterministic JSON
  serialization of the snapshot (stable ordering → cloud prompt-caching stays effective; compact
  → small local models keep it in context).
- **Providers** — `ollama_provider`, `anthropic_provider`, `openai_provider`, `gemini_provider`,
  `rulebased`. Each cloud/local provider lazily imports its SDK and raises a friendly error if
  the extra/key is missing; `parsing.py` extracts and validates the model's JSON into an
  `AnalystVerdict`.
- **`factory.py`** — `build_provider_with_fallback()` constructs the configured provider and, on
  any failure, returns the rule-based provider with a note (surfaced as a snapshot warning).

### 5. Backtesting — `src/indi_analyst/backtest/`

A consumer of the deterministic core (like the screener), not a new analytical layer. Walk-forward
replay of the real pipeline over history:

- **`replay.py`** — `snapshot_at(df, i)` computes `technical.compute(df.iloc[:i+1])`, which is
  look-ahead-free (indicators read only trailing windows / the last bar). **Technical-only**:
  fundamentals/news are not point-in-time available from the free source, so they are left empty.
- **`simulator.py`** — `simulate_symbol` walks each bar: `score` → on an entry action, enter at the
  **next** bar's open, then `resolve_exit` walks to the frozen `compute_levels` stop / first target /
  max-hold timeout (same-bar stop+target books the stop, conservatively).
- **`metrics.py`** — win rate, expectancy (mean R), profit factor, drawdown, and a buy-and-hold
  benchmark; sliced by action and conviction.
- **`engine.py`** — `run_backtest(target)` resolves a symbol or a screener universe, fetches
  multi-year history per symbol (shared rate-limited source), and aggregates. Per-symbol failures
  isolate like `scan_universe`.

### 6. Interfaces

- **`app/dashboard.py`** — Streamlit: ticker input, provider selector, candlestick + RSI/MACD
  charts (Plotly), fundamentals, news, a recommendation card, a per-stock **Macro overlays** panel,
  and a **Sector tailwinds** table in Screener mode.
- **`cli.py`** — `indi-analyst <ticker> [--provider ...]`, a rich terminal report (incl. a `MACRO
  OVERLAYS` block). Also the quickest way to smoke-test the pipeline. Subcommands `screen` (with an
  optional top-down `--sectors-summary` ranking) and `backtest` reuse the same core.

---

## Core models (`models.py`)

| Model | Role |
| --- | --- |
| `Fundamentals` | P/E, P/B, P/S, ROE, D/E, margins, growth, EPS, book value/sh, dividend rate, next results date, sector… (all optional — free data is patchy) |
| `CorporateActions` | Free dividend/split history — paying-years count over a lookback window, last dividend, last split ratio/date, `recent_split` flag (optional; `None` when the source has no action history) |
| `SectorMacroSignal` | A sector-keyed macro overlay (`kind` = budget / rate / …) — normalized −1..+1 `tailwind`, plain-English drivers, source citations, `as_of` (one per firing overlay; none when the sector is unmapped or the pack is absent/disabled) |
| `TechnicalSignals` | Latest-bar indicators + trend/level context |
| `NewsItem` | Headline + VADER sentiment |
| `StockSnapshot` | **The deterministic source of truth** — everything above, plus `corporate_actions`, `macro_signals` (with a `budget_signal` convenience accessor), warnings, and `data_source` / `data_as_of` provenance |
| `TradeLevels` | Entry band, stop, T1/T2, risk-reward |
| `Valuation` | Blended fair value, low/high range, margin of safety, rating + per-method breakdown |
| `QuantScore` | Action, conviction, 0–100 score + component scores + `macro_adjustment` + reasons |
| `AnalystVerdict` | The LLM's structured output: thesis, risks, catalysts, summary |
| `Recommendation` | Final merged object the UI renders |

---

## Extension points

The design is deliberately open at three seams:

1. **New data source** — implement the `PriceSource` / `NewsSource` protocol from
   `datasources/base.py`, then either inject it into `build_snapshot(...)` / `analyze(...)` or
   register it in `datasources/factory.py`. New adapters must document coverage, delay, free-tier
   limits, terms, and failure behavior before becoming a default.
2. **New LLM provider** — implement `verdict(...)` from `llm/base.py` and register it in
   `llm/factory.py`. Reuse `prompts.serialize()` and `parsing.parse_verdict()`.
3. **New scoring/level logic** — `scoring.py` and `levels.py` are pure functions over a
   `StockSnapshot`; tune the weights/thresholds in `config.py` or swap the functions wholesale.

Because the engine returns a plain `Recommendation` and the analysis functions are pure over a
`StockSnapshot`, consumers layer on top with no core changes: a different UI (the planned FastAPI + JS
frontend), the batch **screener**, and the **backtester** — which replays those same pure functions
bar by bar over history — are all thin layers over the deterministic core.
