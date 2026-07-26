# Methodology

How `indi-analyst` turns raw prices into a **score**, an **action**, and **trade levels** — all
deterministically, before any LLM runs. Everything here lives in
`indicators/technical.py`, `analysis/scoring.py`, and `analysis/levels.py`, and every parameter
is tunable in `config.py` / `.env`.

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
```

Technicals drive **timing** (60%); fundamentals drive **conviction** (40%).

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

## Sentiment

Google News RSS headlines for the company are scored with **VADER** (`compound`, −1…1). Coverage
is widened with a couple of query phrasings, near-duplicate headlines are collapsed, and results
are returned freshest-first. The aggregate that nudges the technical score is a **recency-weighted
mean**: each headline's weight halves every `NEWS_RECENCY_HALFLIFE_DAYS` days (default 7), so a
stale headline counts for less than this morning's. Undated headlines get unit weight, and a
half-life of `0` reduces the aggregate to a plain mean. The ±0.2 thresholds and ±3 score nudge are
unchanged. It's a lightweight signal, not a deep NLP model — see [roadmap.md](roadmap.md) for more.

---

## Limitations (read this)

- Yahoo data is **~15 minutes delayed** and fundamentals coverage varies by stock.
- Swing-based support/resistance is a heuristic, not order-book depth.
- Scoring weights are sensible defaults, **not** fitted/backtested — backtesting is on the
  [roadmap](roadmap.md).
- **Not investment advice.** Verify independently before trading.
