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
  `RELIANCE.NS` then `.BO`), fetches OHLCV history and fundamentals from Yahoo Finance.
- **`nse_source.py`** — `NSERealtimeSource`, a composite `PriceSource` that keeps history +
  fundamentals from a yfinance fallback but **overlays a live NSE quote onto the latest bar**.
  Cookie-primes NSE's JSON quote API; any failure returns `None` and leaves the yfinance data
  untouched (never raises). Opt-in via config/flag.
- **`news.py`** — Google News RSS headlines, scored with VADER sentiment.
- **`base.py`** — `PriceSource`, `FundamentalsSource`, `NewsSource` `Protocol`s. Depending on
  the *shape* (not the concrete class) is what lets tests inject a mock source with zero network,
  and let the NSE-direct source drop in without touching the engine.
- **`factory.py`** — `build_price_source(name, settings)` selects `yfinance` (default) or `nse`
  from config, mirroring `llm/factory.py`. `snapshot.build_snapshot` calls it as the default, so
  `DEFAULT_PRICE_SOURCE` flows to both single-stock analyze and the screener.

### 2. Indicators — `src/indi_analyst/indicators/technical.py`

The core indicator set (RSI, MACD, SMA/EMA 20/50/200, Bollinger, ADX, ATR, 52-week position,
volume ratio, swing support/resistance, trend classification) computed directly with
**pandas/numpy**. No `pandas-ta` dependency — it avoids that library's numpy/pandas version
breakage on Python 3.13 and keeps the math auditable. See
[methodology.md](methodology.md) for definitions.

### 3. Analysis — `src/indi_analyst/analysis/`

- **`snapshot.py`** — orchestrates fetch + compute into a `StockSnapshot` (sources are
  injectable for testing).
- **`levels.py`** — deterministic **entry zone**, **ATR-based stop-loss** (widened to structural
  support, never tightened below the ATR floor), and **risk-reward targets** snapped to distinct
  resistances.
- **`scoring.py`** — a transparent composite: **60% technical + 40% fundamental** → an `Action`
  (BUY/ACCUMULATE/HOLD/AVOID/SELL) and a conviction, with a `reasons` list.
- **`engine.py`** — the top-level entry point: `analyze(query, provider=...)` runs the whole
  pipeline and returns a `Recommendation`. Also `analyze_snapshot(...)` if you already have one.

### 4. LLM layer — `src/indi_analyst/llm/`

- **`base.py`** — the whole contract is one method:
  `verdict(snapshot, levels, quant) -> AnalystVerdict`.
- **`prompts.py`** — the "lead investment-banker" system prompt + a deterministic JSON
  serialization of the snapshot (stable ordering → cloud prompt-caching stays effective; compact
  → small local models keep it in context).
- **Providers** — `ollama_provider`, `anthropic_provider`, `openai_provider`, `gemini_provider`,
  `rulebased`. Each cloud/local provider lazily imports its SDK and raises a friendly error if
  the extra/key is missing; `parsing.py` extracts and validates the model's JSON into an
  `AnalystVerdict`.
- **`factory.py`** — `build_provider_with_fallback()` constructs the configured provider and, on
  any failure, returns the rule-based provider with a note (surfaced as a snapshot warning).

### 5. Interfaces

- **`app/dashboard.py`** — Streamlit: ticker input, provider selector, candlestick + RSI/MACD
  charts (Plotly), fundamentals, news, and a recommendation card.
- **`cli.py`** — `indi-analyst <ticker> [--provider ...]`, a rich terminal report. Also the
  quickest way to smoke-test the pipeline.

---

## Core models (`models.py`)

| Model | Role |
| --- | --- |
| `Fundamentals` | P/E, P/B, ROE, D/E, margins, growth, sector… (all optional — free data is patchy) |
| `TechnicalSignals` | Latest-bar indicators + trend/level context |
| `NewsItem` | Headline + VADER sentiment |
| `StockSnapshot` | **The deterministic source of truth** — everything above, plus warnings |
| `TradeLevels` | Entry band, stop, T1/T2, risk-reward |
| `QuantScore` | Action, conviction, 0–100 score + component scores + reasons |
| `AnalystVerdict` | The LLM's structured output: thesis, risks, catalysts, summary |
| `Recommendation` | Final merged object the UI renders |

---

## Extension points

The design is deliberately open at three seams:

1. **New data source** — implement the `PriceSource` / `NewsSource` protocol from
   `datasources/base.py`, then either inject it into `build_snapshot(...)` / `analyze(...)` or
   register it in `datasources/factory.py` so it's selectable via `DEFAULT_PRICE_SOURCE` /
   `--price-source`. (This is exactly how `nse_source.py`'s real-time overlay landed.)
2. **New LLM provider** — implement `verdict(...)` from `llm/base.py` and register it in
   `llm/factory.py`. Reuse `prompts.serialize()` and `parsing.parse_verdict()`.
3. **New scoring/level logic** — `scoring.py` and `levels.py` are pure functions over a
   `StockSnapshot`; tune the weights/thresholds in `config.py` or swap the functions wholesale.

Because the engine returns a plain `Recommendation`, a different UI (the planned FastAPI + JS
frontend) or a batch **screener** is a thin layer on top — no core changes required.
