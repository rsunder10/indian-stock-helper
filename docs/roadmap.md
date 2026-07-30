# Roadmap

Where `indi-analyst` is headed. The MVP is a **single-stock deep-dive analyzer**; the north star
is a **facts-based, provider-agnostic research assistant** that not only analyzes a stock you name
but **surfaces the ones worth looking at** — while staying honest, explainable, and free to run.

The project is now explicitly **free-source first**: the core must work without an exchange-data
subscription, broker account, paid API key, or fragile website scraping. This means the product
targets delayed/EOD analysis rather than promising exchange-grade real-time quotes.

The architecture was built for this: the engine returns a plain `Recommendation`, data sources
and LLM providers sit behind protocols, so most of what follows layers on **without touching the
core**.

Status legend: ✅ done · 🔜 next · 🧭 planned · 💡 idea

---

## ✅ Phase 0 — MVP (done)

- Deterministic engine: yfinance + Google News → indicators → snapshot → levels → quant score.
- Provider-agnostic LLM layer (Ollama default, Claude, OpenAI-compatible, Gemini, rule-based
  fallback).
- Streamlit dashboard + CLI.
- Test suite (unit + end-to-end, no network) and live validation on real tickers.

---

## ✅ Phase 1 — Screener / recommender (done)

**The headline milestone, delivered: go from "analyze this stock" to "which stocks should I look
at?"** Lives in `src/indi_analyst/screener/`, layered on the engine without touching it.

- ✅ **Universe & watchlists** — bundled NIFTY 50 fallback plus `watchlist:SYM1,SYM2` and
  `file:/path.csv` universes. The free-first product path uses bundled/local constituents and cached
  snapshots without live index refresh.
- ✅ **Batch scoring** — `scan_universe` runs the deterministic engine + per-stock verdict across a
  universe with a **concurrent thread pool** and a **snapshot cache**, ranked by quant score.
- ✅ **Filters & presets** — screen by action, conviction, sector, valuation (P/E), and risk-reward;
  named presets `high-conviction-buys`, `oversold-quality`, `breakout-with-fundamentals`.
- ✅ **Shortlist digest** — a ranked "most compelling ideas" digest built from the top rows'
  (already LLM-grounded) theses and numbers — grounded, never picking from thin air.
- ✅ **Ranked results view** — CLI table (`indi-analyst screen`) and a Streamlit *Screener* mode
  with a sortable table + one-click drill-down into the full deep dive.
- ✅ **Persistence/enabler** — a **SQLite** layer caches snapshots + constituents and records every
  scan, so re-scans are fast and runs can be **diffed over time** (`ScanCache.diff_scans`).

*Remaining polish (future):* parquet export, richer sort/column controls, and an LLM-authored
(rather than assembled) comparative digest once a generic completion hook exists on providers.

---

## 🔜 Phase 2 — Free-source hardening and data quality

The next milestone is reliability, not lower latency.

- ✅ **Harden the yfinance path** — `history()` now re-sorts non-chronological bars, drops rows with
  missing OHLC / non-positive prices / inconsistent High-Low, records source + as-of provenance
  (`data_source` / `data_as_of`), and surfaces data-quality warnings (CLI `NOTES`, dashboard
  warnings) instead of allowing `NaN`/garbage prices into a recommendation.
- ✅ **Free-source cache and rate discipline** — SQLite snapshot cache (Phase 1) plus a dependency-free
  `RateLimiter` + `retry`/backoff on every yfinance call; a single shared limiter paces a scan's
  worker threads so normal use does not hammer public endpoints. (Parquet snapshots remain an
  optional future nicety.)
- ✅ **Local universe packs** — versioned NIFTY 50/200/500 constituent CSVs ship inside the package
  (`src/indi_analyst/data/`), refreshable from the free NSE Indices lists via
  `scripts/refresh_universes.py`; users can still supply their own via `file:` / `watchlist:`. A
  scan resolves symbols from a bundled pack (fresh cache → stale cache → the index's own pack → the
  NIFTY 50 pack with a warning) and never needs a live exchange request just to discover symbols.
- 🔜 **Optional low-volume adapter experiments** — evaluate providers with an explicit free tier,
  such as Alpha Vantage's daily/BSE sample coverage, but keep them opt-in because free quotas and
  exchange coverage can change. No provider becomes a hard dependency until its limits and terms are
  verified.
- ✅ **Deeper free fundamentals** — yfinance's per-share figures (EPS, book value, dividend rate,
  revenue/share, P/S) and the **next results date** flow into the snapshot, valuation, and UI;
  valuation prefers reported EPS/book value over ratio-derived ones. **Free corporate-action
  history** (dividends + splits from `Ticker.actions`) now rides on the snapshot: a
  dividend-consistency count *gates* the dividend-discount model to genuine payers, and recent
  splits raise a data-quality warning. Public-company filings and annual/quarterly-report parsing
  are deliberately deferred to **Phase 4** — they need fragile scraping or a paid source, which
  breaks the free-source-first, no-scraping guarantee; per-share figures already cover the
  high-value fundamentals.
- ✅ **Richer sentiment** — Google News coverage widened with multiple query phrasings and
  duplicate-headline collapsing, returned freshest-first, with a **recency-weighted** aggregate
  sentiment (weight halves every `NEWS_RECENCY_HALFLIFE_DAYS`) — still the no-key Google News RSS
  baseline.

---

## 🧭 Phase 3 — Product & UX

- **FastAPI + JS frontend** — promote the reusable core to a proper API + web app (multi-page,
  faster, shareable), with Streamlit remaining the quick-iteration playground. Include visible source,
  timestamp, delay, and data-quality status on every analysis.
- **Report export** — one-click **PDF / Markdown** research note per stock (and per screen).
- **Alerts** — price/level/score-change alerts on watchlists (email/Telegram/desktop).
- **Portfolio view** — track holdings, aggregate exposure, and per-position level monitoring.
- **Peer & sector comparison** — side-by-side metrics vs sector peers.

---

## 🧭 Phase 4 — Rigor & trust

- **Backtesting the level logic** — ✅ *done (initial harness).* A walk-forward backtester
  (`src/indi_analyst/backtest/`, `indi-analyst backtest`) replays the deterministic pipeline over
  history and reports win rate, expectancy, R-multiples, profit factor, and a buy-and-hold benchmark.
  It is technical-only (fundamentals/news are not point-in-time available from the free source). Still
  open: **using** these results to turn scoring weights from sensible defaults into **evidence-tuned**
  parameters, plus intraday/slippage/cost modelling.
- **Configurable strategies** — multiple scoring/level profiles (value, momentum, swing,
  positional) selectable per run.
- **Explainability & audit** — persist the full snapshot + reasons for every recommendation so
  any call can be reconstructed and reviewed.
- **Evaluation harness** — track recommendation outcomes over time to keep the framework honest.
- **Filings-grade fundamentals** (deferred from Phase 2) — public-company filings and
  annual/quarterly reports, only where a source is legally accessible and each figure can be cited.
  Held here because it needs more than a free no-key endpoint; the Phase 2 path stops at yfinance's
  per-share figures + corporate actions.

---

## 💡 Backlog / ideas

- Options context (IV, PCR) for large caps.
- Multi-language / regional-name ticker resolution.
- Scheduled scans (cron) that email a daily/weekly shortlist.
- Packaging: publish to PyPI; a one-command Docker image; CI (lint + tests) on every push.
- Pluggable "house view" prompts per provider (conservative vs aggressive analyst personas).
- Confidence calibration: show how much of a call is technical vs fundamental vs sentiment.

---

## Design guarantees we keep

Whatever gets added, these hold:

1. **Deterministic core stays LLM-free** — you always get numbers, even offline.
2. **No hard dependency on any one model** — providers stay swappable, with rule-based fallback.
3. **Everything explainable** — every score and level has a stated reason.
4. **Free-source first** — the default path never requires a paid key, broker account, or exchange
   subscription; it does not claim real-time accuracy.
5. **Data honesty** — every recommendation carries source, timestamp, freshness, and quality warnings.

---

*Contributions/ideas welcome. If you're picking something up, the extension points in
[architecture.md](architecture.md#extension-points) are the place to start.*
