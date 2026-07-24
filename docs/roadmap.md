# Roadmap

Where `indi-analyst` is headed. The MVP is a **single-stock deep-dive analyzer**; the north star
is a **facts-based, provider-agnostic research assistant** that not only analyzes a stock you name
but **surfaces the ones worth looking at** — while staying honest, explainable, and free to run.

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

- ✅ **Universe & watchlists** — NIFTY 50 / 200 / 500 fetched **live** from NSE and cached, plus
  `watchlist:SYM1,SYM2` and `file:/path.csv` universes. A bundled NIFTY 50 is the offline fallback.
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

## 🧭 Phase 2 — Better, fresher data

- ✅ **NSE-direct real-time quotes** — `NSERealtimeSource` (`datasources/nse_source.py`) hits NSE's
  quote API for a near-live price and **overlays it onto the latest yfinance bar**, so `last_close`,
  `change_pct`, and latest-bar indicators are fresh. Opt-in (`--price-source nse`, dashboard toggle,
  or `DEFAULT_PRICE_SOURCE=nse`); **degrades silently to yfinance** on any NSE failure (403, offline,
  non-India IP). Selected via a new `datasources/factory.py`, works for single-stock analyze and the
  screener (which bypasses the snapshot cache when live).
- **Deeper fundamentals** — quarterly results, corporate actions, promoter/institutional holding,
  results calendar (e.g. screener.in-style scraping) behind a `FundamentalsSource`.
- **Corporate-actions & earnings awareness** — flag upcoming results / ex-dividend dates as
  catalysts and as risk windows.
- **Richer sentiment** — beyond headlines: earnings-call tone, filings, and optionally social
  signals; swap VADER for a stronger model where it helps.

---

## 🧭 Phase 3 — Product & UX

- **FastAPI + JS frontend** — promote the reusable core to a proper API + web app (multi-page,
  faster, shareable), with Streamlit remaining the quick-iteration playground.
- **Report export** — one-click **PDF / Markdown** research note per stock (and per screen).
- **Alerts** — price/level/score-change alerts on watchlists (email/Telegram/desktop).
- **Portfolio view** — track holdings, aggregate exposure, and per-position level monitoring.
- **Peer & sector comparison** — side-by-side metrics vs sector peers.

---

## 🧭 Phase 4 — Rigor & trust

- **Backtesting the level logic** — measure how entry/stop/target rules would have performed
  historically; turn scoring weights from sensible defaults into **evidence-tuned** parameters.
- **Configurable strategies** — multiple scoring/level profiles (value, momentum, swing,
  positional) selectable per run.
- **Explainability & audit** — persist the full snapshot + reasons for every recommendation so
  any call can be reconstructed and reviewed.
- **Evaluation harness** — track recommendation outcomes over time to keep the framework honest.

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
4. **Free-source friendly** — the default path never requires a paid key.

---

*Contributions/ideas welcome. If you're picking something up, the extension points in
[architecture.md](architecture.md#extension-points) are the place to start.*
