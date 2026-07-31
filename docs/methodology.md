# Methodology

How `indi-analyst` turns raw prices into a **score**, an **action**, **trade levels**, and a
**fair value** — all deterministically, before any LLM runs. Everything here lives in
`indicators/technical.py`, `analysis/scoring.py`, `analysis/levels.py`, and
`analysis/valuation.py`, and every parameter is tunable in `config.py` / `.env`.

> This is a transparent, rules-based framework — not a predictive model. Treat outputs as a
> structured starting point for your own research, not a forecast.

---

## Indicators (`indicators/technical.py`)

Computed with pandas/numpy from the OHLCV history (default `1y`).

| Indicator | Definition | Used for |
| --- | --- | --- |
| **RSI (14)** | Wilder's RSI (EWM of gains/losses) | Overbought (>70) / oversold (<30) momentum |
| **MACD (12,26,9)** | EMA12 − EMA26, with 9-EMA signal + histogram | Momentum direction (line vs signal) |
| **SMA 20 / 50 / 200** | Simple moving averages | Trend structure, golden/death cross |
| **EMA 20** | 20-period exponential MA | Pullback reference for entries |
| **Bollinger (20, 2σ)** | 20-SMA ± 2 standard deviations | Volatility envelope |
| **ADX (14)** | Wilder's ADX | Trend *strength* (>25 = strong) |
| **ATR (14)** | Wilder's Average True Range | Volatility → stop distance |
| **52-week position** | `(price − 52w low) / (52w high − 52w low)` | Where price sits in its yearly range |
| **Volume ratio** | volume ÷ 20-day avg volume | Participation / confirmation |
| **Support / resistance** | Recent swing highs/lows (pivot scan), split below/above price | Entry, stop, and target anchors |
| **Trend** | From price vs 200-SMA and 50/200 cross | `uptrend` / `downtrend` / `sideways` |

---

## Quant score (`analysis/scoring.py`)

Two 0–100 sub-scores, each starting at a neutral **50** and adjusted by rules, then combined.
Every adjustment appends a human-readable reason (surfaced in the UI and, in rule-based mode, as
the thesis).

### Technical sub-score

| Signal | Adjustment |
| --- | --- |
| Price above / below 200-SMA | +8 / −8 |
| Golden cross / death cross (50 vs 200) | +6 / −6 |
| RSI < 30 / > 70 / healthy 45–60 | +8 / −8 / +3 |
| MACD above / below signal | +5 / −5 |
| ADX > 25, aligned with / against the uptrend | +4 / −4 |
| Near 52-week high (>0.9) / low (<0.2) | −3 / +3 |
| News sentiment > +0.2 / < −0.2 | +3 / −3 |

### Fundamental sub-score

| Signal | Adjustment |
| --- | --- |
| ROE > 18% / < 8% | +8 / −6 |
| Debt/Equity < 0.5 / > 1.5 | +6 / −8 |
| Revenue growth > 15% / negative | +7 / −6 |
| Net margin > 15% / negative | +5 / −8 |
| P/E in (0, 15) / > 60 | +5 / −5 |

(Free fundamentals are patchy; if none are available the score is technical-only and says so.)

### Combining → action & conviction

```
composite = 0.60 × technical + 0.40 × fundamental
composite += macro_adjustment           # bounded ±MACRO_MAX_POINTS, see "Macro overlays"
```

Technicals drive **timing** (60%); fundamentals drive **conviction** (40%). A small, bounded
**macro** nudge is then added from government-open-data overlays (Union Budget, RBI rate cycle) —
see [Macro overlays](#macro-overlays). It is a conviction tiebreaker, not a timing signal, combined
across all overlays under one cap (default ±6 points), and is `0` whenever no overlay applies.

| Composite | Action |
| --- | --- |
| ≥ 68 | **BUY** |
| ≥ 57 | **ACCUMULATE** |
| ≥ 45 | **HOLD** |
| ≥ 35 | **AVOID** |
| < 35 | **SELL** |

**Conviction** from distance off neutral (`|composite − 50|`): ≥ 18 → HIGH, ≥ 8 → MEDIUM, else LOW.

---

## Trade levels (`analysis/levels.py`)

Computed from ATR and market structure. `k` = `ATR_STOP_MULTIPLE` (default 1.8); if ATR is
missing it falls back to 2% of price.

### Entry zone

- **Uptrend:** favor a shallow pullback — band runs from `min(EMA-20, nearest support)` up to the
  current price (buy strength on a dip, not chasing).
- **Otherwise:** a tight band around the current price, `price ± 0.4·ATR`.

`entry_mid` is the midpoint used for the risk math.

### Stop-loss

- Start with a **volatility stop**: `entry_mid − k·ATR`.
- **Only ever widen** it to sit just below a structural support (if one is within `0.75·ATR`
  below the raw stop, place it at `support − 0.15·ATR`). It is never tightened above the ATR
  floor — that would shrink risk toward zero and distort the targets.
- `risk = entry_mid − stop_loss`.

### Targets

- `Target 1 = entry_mid + TARGET1_RR × risk` (default 2R)
- `Target 2 = entry_mid + TARGET2_RR × risk` (default 3.5R)
- Each target is snapped to a **distinct** nearby resistance if one sits within 2%; an ordering
  guard guarantees `T2 > T1` (snapping can never collapse them).
- `risk_reward = (T1 − entry_mid) / risk`.

The result is a coherent plan — entry band, a stop that respects volatility *and* structure, and
laddered targets — that holds up whether the narrative comes from an LLM or the rule-based engine.

---

## Fair value (`analysis/valuation.py`)

An intrinsic-value estimate — "what is it worth?" — computed deterministically before any LLM
runs, alongside the trade levels. A full DCF is impossible on free yfinance data (no cash-flow
statements), so we blend up to three cheap, transparent methods built from ratios we already have.
Each is a different lens; equal-weighting them keeps any single one from dominating.

### Inputs

- **EPS** and **book value per share (BVPS)** use the source's reported per-share figures when
  present; otherwise they're backed out of the ratios (`eps = price / P/E`, `bvps = price / P/B`),
  so nothing extra needs fetching.
- **Growth** prefers earnings growth, falling back to revenue growth.

### Methods

| Method | Formula | Runs when |
| --- | --- | --- |
| **Graham number** | `√(22.5 · EPS · BVPS)` — a conservative value floor | EPS > 0 and BVPS > 0 |
| **Earnings power** | fair P/E × EPS, where fair P/E ≈ growth% (PEG≈1), clamped to `[FAIR_PE_FLOOR, FAIR_PE_CAP]` = `[10, 35]`; `FAIR_PE_BASE` (15) when growth is unknown | EPS > 0 |
| **Dividend discount** | Gordon growth: `D₀·(1+g) / (r − g)`, with `r` = `FAIR_VALUE_DISCOUNT_RATE` (12%) and `g` capped at `FAIR_VALUE_TERMINAL_GROWTH` (5%) | dividend payer, `r − g ≥ 2%`, **and a consistent payout** (see below) |

**Dividend-consistency gate.** The Gordon model assumes a *durable* payout, so a one-off or erratic
dividend shouldn't drive a fair value. When free corporate-action history is available (see
[Corporate actions](#corporate-actions)), the method runs only for demonstrated consistent
payers — dividends in at least `DIVIDEND_MIN_CONSISTENT_YEARS` (default 3) of the last
`CORPORATE_ACTION_LOOKBACK_YEARS` (default 6) years. Below that it's skipped with a stated reason.
When no corporate-action history is available, behaviour is unchanged (the method runs on yield
alone). Note this deliberately skips young-but-consistent payers with less than the threshold of
years listed — the perpetuity model needs a track record.

### Blending → fair value, margin, confidence

- Each method's estimate is **sanity-bounded** to `[0.1×, 10×]` the current price; an outlier is
  dropped (with a reason) so one wild number can't distort the blend.
- The **fair value** is the simple average of the surviving methods; `low` / `high` are their
  min / max.
- **Margin of safety** = `(fair_value − price) / price`. Beyond ±`MARGIN_OF_SAFETY` (default 15%)
  it flips the rating to **Undervalued** / **Overvalued**; inside the band it's **Fairly valued**.
- **Confidence** tracks how many methods ran: 3 → HIGH, 2 → MEDIUM, 1 → LOW.

If no method can run (P/E, P/B, and dividend data all missing), an empty `Valuation` is returned
with a reason — never an exception. Every parameter above is tunable in `config.py` / `.env`.

---

## Corporate actions

Free dividend + split history comes from a single `yfinance` `Ticker.actions` call (no scraping,
no key), parsed into a `CorporateActions` object on the snapshot. It is **optional**: a source
without it, or a stock with no action history, leaves `corporate_actions` `None`.

- **Dividend consistency** — distinct calendar years with a dividend inside a
  `CORPORATE_ACTION_LOOKBACK_YEARS` window, used only to gate the dividend-discount method (above).
- **Splits** — the most recent split ratio + date; a split within `SPLIT_RECENCY_DAYS` of the data
  as-of date is flagged `recent_split` and surfaces a snapshot warning (pre-split levels can look
  discontinuous). Split recency is measured against the **data's** freshness, not wall-clock time,
  so results are deterministic.

Corporate actions never fabricate a value: absence is preserved as `None`, and the only place they
change a number is by *removing* an unreliable dividend-discount estimate from the blend.

---

## Sentiment

Google News RSS headlines for the company are scored with **VADER** (`compound`, −1…1). Coverage
is widened with a couple of query phrasings, near-duplicate headlines are collapsed, and results
are returned freshest-first. The aggregate that nudges the technical score is a **recency-weighted
mean**: each headline's weight halves every `NEWS_RECENCY_HALFLIFE_DAYS` days (default 7), so a
stale headline counts for less than this morning's. Undated headlines get unit weight, and a
half-life of `0` reduces the aggregate to a plain mean. The ±0.2 thresholds and ±3 score nudge are
unchanged. It's a lightweight signal, not a deep NLP model — see [roadmap.md](roadmap.md) for more.

---

## Macro overlays

Government open data expresses *sector-level* tailwinds and headwinds — where public money and
policy are flowing. `indi-analyst` folds these into a small, bounded, explainable nudge on the
composite score through a uniform **macro-overlay framework** (`analysis/macro.py`): each source is a
resolver that maps a stock's sector to a normalized −1..+1 `tailwind` from a **bundled, versioned
pack**, and the framework combines them under one shared cap. Everything is deterministic and
computed before any LLM runs. Two overlays ship today (Budget, RBI rate cycle); IIP and forex are
candidates on the [roadmap](roadmap.md).

**Free-source, offline at runtime.** Every pack lives in `src/indi_analyst/data/` and is refreshed at
*build time* from free machine-readable sources (the data.gov.in OGD API, RBI MPC decisions) via
`scripts/refresh_*.py` — the analysis path never makes a live call, mirroring the NSE universe packs.
Each pack's **sector crosswalk** and the transforms below are **maintained config**, not scraped, so
a raw feed can never silently distort the signal. Sector matching is case-insensitive
exact-then-substring and each crosswalk carries **both** taxonomies a sector may arrive in — the NSE
"Industry" strings (bundled universe CSVs) and the coarser yfinance GICS-like sectors. An
unmapped/`None` sector or a disabled/absent pack contributes **nothing** (missing stays missing).

**Combining → `macro_adjustment`.** Each overlay contributes
`clamp(tailwind × per-source cap, ±per-source cap)`; the sum is then clamped to `MACRO_MAX_POINTS`
(default ±6) so the overlays together stay small relative to the technical/fundamental core and keep
the 50-centred scale — and the action cutoffs (68/57/45/35) — intact. Every contributing point
appends a human-readable `reasons` line (e.g. *"+5.0 pts — Union Budget 2023-24 sector tailwind:
Railways outlay +75% YoY"*); `QuantScore.macro_adjustment` exposes the exact combined points.

### Budget overlay (`analysis/budget.py`)

The Union Budget's allocation by head (Defence, Railways, Green energy, Housing, …). Pack
`data/budget_<year>.json` (selected by `BUDGET_YEAR`; refreshed by `scripts/refresh_budget.py`). For
the heads a sector maps to:

```
tailwind = clamp( mean(head YoY%) / BUDGET_YOY_SCALE , −1, +1 )     # BUDGET_YOY_SCALE default 20
per-source cap = BUDGET_MAX_POINTS (5)
```

A growing allocation is a structural sector tailwind; every point traces to a published figure. The
shipped `budget_2023-24.json` holds **real data.gov.in figures** (Central-Sector-Scheme BE totals, the
latest ministry-wise dataset there); a maintained `head_aliases` map pins each short head to the
dataset's exact ministry name so the refresh can align them.

### RBI rate-cycle overlay (`analysis/rates.py`)

The rate cycle drives Indian sector rotation. Pack `data/rates_<version>.json` (selected by
`RATE_PACK_VERSION`; refreshed by `scripts/refresh_rates.py`) carries the repo rate, the previous
repo rate, MPC stance, CPI, and a maintained **rate-sensitivity** crosswalk (signed magnitude per
sector — realty/autos/NBFC/capital-goods most sensitive; IT/pharma/defensives ≈ 0).

```
regime_sign = +1 easing (repo cut)  |  −1 tightening (repo hike)  |  0 neutral (from stance)
tailwind    = clamp( regime_sign × sector_sensitivity , −1, +1 )
per-source cap = RATE_MAX_POINTS (4)
```

So an easing cycle is a tailwind for rate-sensitive sectors and a headwind for none of the
insensitive ones; a tightening cycle flips the sign. The regime is **derived** from repo vs the
previous repo, so refreshing just those numbers updates the direction automatically.

**Inert in the backtest.** The backtester replays snapshots with an **empty** `Fundamentals` (no
sector), so no overlay fires and `macro_adjustment` is `0`. This is deliberate and honest: there are
no point-in-time historical macro packs, so applying today's tailwind to past bars would be
look-ahead bias. The backtest therefore measures the technical signal alone, unchanged by this
feature.

The overlays also feed the **narrative**: the rule-based verdict adds each positive tailwind as a
catalyst (and each negative one as a headwind risk), and the LLM prompt receives a `macro_overlays`
block it must treat as macro context, never as per-company numbers. Every parameter is tunable in
`config.py` / `.env`; each overlay has its own `*_ENABLED` switch.

---

## Backtesting

The backtester replays the **real** deterministic pipeline over history so the signal can be judged
on evidence rather than intuition. It reuses the exact production functions — no parallel
re-implementation — which is only sound because the indicators are look-ahead-free: `technical.compute`
reads only trailing windows and the last bar, so computing it on `df.iloc[: i + 1]` reproduces
precisely the snapshot an analyst would have had at bar `i`. (A regression test appends future bars and
asserts the bar-`i` snapshot is unchanged.)

**Technical-only, by design.** The free source only exposes *current* fundamentals and news, so
backfilling them into history would be look-ahead bias. Each replayed snapshot therefore carries the
computed technicals, an **empty** `Fundamentals`, and no news sentiment. The composite score's
fundamental half sits at its neutral 50, so the backtest measures the **technical timing signal**
(trend / RSI / MACD / ADX / levels) honestly, and never claims to have validated the fundamental score.
The report header states this explicitly.

**Walk-forward loop** (per symbol):

1. After a warm-up (`BACKTEST_WARMUP_BARS`, default 200 — enough for SMA-200), at each bar `i` build
   the point-in-time snapshot and run `score`.
2. If the action is an **entry action** (`BACKTEST_ENTRY_ACTIONS`, default `BUY,ACCUMULATE`) and no
   position is open, enter a long at **bar `i+1`'s open** — never the signal bar's close, which the
   decision already used.
3. Freeze the deterministic `compute_levels` stop and first target. Walk forward: on each bar, a low
   piercing the stop exits at the stop; otherwise a high reaching the target exits at the target. If a
   single bar touches **both**, the stop is booked first (conservative — an ambiguous bar is never a
   win). After `BACKTEST_MAX_HOLD_BARS` (default 40) the trade is closed at that bar's close
   (`timeout`). Setups whose next open has already gapped through the stop or the target are skipped.
4. One position at a time; scanning resumes after the exit bar.

**Metrics.** Each trade records its return, R-multiple (reward in units of the entry-to-stop risk),
bars held, and exit reason. Aggregates: win rate, average win/loss, mean return, **expectancy** (mean
R), **profit factor** (gross win ÷ gross loss), and the deepest drawdown of the pooled equity curve.
Everything is read against a **buy-and-hold benchmark** — the mean per-symbol close-to-close return over
the same window — so an edge is only real if it beats simply holding. Results are also sliced by entry
action and conviction. It is a bar-resolution model (no intraday path, slippage, or costs), so treat it
as a relative sanity check on the rules, not a live P&L promise.

---

## Limitations (read this)

- Yahoo data is **~15 minutes delayed** and fundamentals coverage varies by stock.
- Swing-based support/resistance is a heuristic, not order-book depth.
- Scoring weights are sensible defaults, **not** fitted. The `backtest` command (see *Backtesting*
  above) now measures how the technical signal + levels would have performed, but the shipped weights
  are not yet tuned to those results.
- **Not investment advice.** Verify independently before trading.
