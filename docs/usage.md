# Usage

Three ways to use `indi-analyst`: the **web dashboard**, the **CLI**, or as a **Python library**.

## Install

```bash
uv sync                      # base install (Ollama + rule-based work with no key)
uv sync --extra anthropic    # add a cloud provider's SDK when you want it
uv sync --extra openai
uv sync --extra gemini
uv sync --extra dev          # pytest, for running the tests
```

Copy the settings template and edit as needed (optional — defaults work offline):

```bash
cp .env.example .env
```

See [providers.md](providers.md) for every configuration key.

---

## Web dashboard

```bash
uv run streamlit run src/indi_analyst/app/dashboard.py
```

Then open http://localhost:8501. In the sidebar: enter a ticker, pick a provider (only
configured ones appear), choose a history window, and press **Analyze**. You get:

- Header metrics: last close, RSI, trend, 52-week position
- A **recommendation card**: action, conviction, score, entry zone, stop, T1/T2, risk-reward
- A **price chart**: candlesticks + SMAs + Bollinger, with RSI and MACD subplots
- Thesis, key risks, catalysts, a plain-English gist
- Expandable fundamentals table and recent news (with sentiment)

Data fetches are cached for 15 minutes (`st.cache_data`).

---

## CLI

```bash
uv run indi-analyst RELIANCE                 # uses the default provider from .env
uv run indi-analyst TCS --provider rulebased # force a specific provider
uv run indi-analyst INFY.NS --provider ollama
```

Accepts a bare symbol (`RELIANCE`), an explicit Yahoo symbol (`RELIANCE.NS` / `.BO`), or
lowercase. Prints a formatted terminal report; exits non-zero on a bad ticker or data error.
(The bare form is shorthand for the `analyze` subcommand — `indi-analyst analyze RELIANCE`.)

---

## Fair value (intrinsic value)

Alongside the trade levels, every analysis computes a deterministic **fair value** — what the
business looks worth, independent of where it's trading — and reports it under a `FAIR VALUE`
block:

```
FAIR VALUE
  Estimate : ₹1,650.00  (range ₹1,480–₹1,910)
  Vs price : +11.2% — Undervalued · HIGH confidence
    · √(22.5 × EPS ₹78.0 × BVPS ₹41.0) = ₹1,690
    · Fair P/E 18 (growth 18%) × EPS ₹78.0 = ₹1,404
    · Gordon: D ₹18.0 × (1+5%) / (12% − 5%) = ₹270
```

It blends whichever of three transparent methods the free data supports — the **Graham number**,
a growth-justified **fair P/E × EPS** (earnings power), and the **Gordon dividend-discount model**
(dividend payers only) — into one estimate plus a low/high range. **Margin of safety** is
`(fair − price)/price`; the **rating** (Undervalued / Fairly valued / Overvalued) flips at the
`MARGIN_OF_SAFETY` band; **confidence** reflects how many methods contributed (3 → HIGH). When the
free source has no usable P/E, P/B, or dividend, the block is simply omitted rather than guessed.

Tune the model via `.env` — `FAIR_VALUE_DISCOUNT_RATE`, `FAIR_VALUE_TERMINAL_GROWTH`,
`FAIR_PE_BASE` / `FAIR_PE_FLOOR` / `FAIR_PE_CAP`, and `MARGIN_OF_SAFETY` (see `.env.example`).

In the screener, fair value surfaces as an **UPSIDE** column and a `--min-upside` filter (e.g.
`--min-upside 0.15` keeps only names trading ≥15% below fair value).

---

## Data-source policy

The supported baseline is free, delayed/EOD-oriented data from yfinance plus Google News RSS. It is
appropriate for research and education, not latency-sensitive trading. Every future source adapter
must document its coverage, delay, quota, terms, and failure behavior.

---

## Screener (scan a universe)

Go from *"analyze this stock"* to *"which stocks should I look at?"*. The `screen` subcommand runs
the **same deterministic engine + per-stock verdict** across a whole universe, ranks by score, and
prints a table (plus an optional top-ideas digest).

```bash
uv run indi-analyst screen --universe nifty50 --provider rulebased --top 15
uv run indi-analyst screen --universe nifty50 --preset high-conviction-buys --digest
uv run indi-analyst screen --universe watchlist:RELIANCE,TCS,INFY --min-score 55 --min-rr 2
uv run indi-analyst screen --universe nifty500 --limit 50 --action BUY,ACCUMULATE --format json
```

**Universes** — `nifty50` / `nifty200` / `nifty500` (using cached or bundled/local constituents),
`watchlist:SYM1,SYM2` (inline), or `file:/path/to/list.csv` (a `Symbol`-column CSV or a
newline/comma symbol list).

**Filters** — `--preset` (`high-conviction-buys`, `oversold-quality`,
`breakout-with-fundamentals`), then narrow further with `--min-score`, `--min-rr`, `--max-pe`,
`--min-upside` (min margin of safety vs fair value), `--action BUY,ACCUMULATE`,
`--sector "Information Technology,Energy"`. `--top N` limits rows shown (`--top 0` = all);
`--limit N` caps how many constituents get scanned.

**Speed & cost** — rule-based is fastest/free and needs no key; a cloud/Ollama provider runs a
full verdict per stock, so start with `--limit` on the big indices. Snapshots are cached
(`snapshot_cache_ttl_hours`), so re-scanning a universe is markedly faster the second time.

**Offline** — scans use cached constituents, the bundled NIFTY 50 list, an inline watchlist, or a
local CSV. No live exchange request is needed to discover symbols.

Every scan is persisted, so you can diff runs over time:

```python
from indi_analyst.screener.cache import ScanCache
from indi_analyst.config import get_settings

cache = ScanCache(get_settings().screener_cache_path)
for d in cache.diff_scans("nifty50"):
    if d["changed"]:
        print(d["symbol"], d["old_action"], "->", d["new_action"], f"({d['score_delta']:+})")
```

In the **dashboard**, switch the sidebar **Mode** to *Screener* to pick a universe, apply a
preset/min-score, run the scan, browse the ranked table, and drill into any row's full deep dive.

---

## As a Python library

The engine returns a plain `Recommendation` pydantic model — ideal for scripts, notebooks, or the
screener/API.

```python
from indi_analyst.analysis.engine import analyze

rec = analyze("RELIANCE", provider="rulebased")

print(rec.action.value)              # e.g. "HOLD"
print(rec.conviction.value)          # "LOW" | "MEDIUM" | "HIGH"
print(rec.quant.score)               # 0..100
print(rec.levels.entry_low, rec.levels.entry_high)
print(rec.levels.stop_loss, rec.levels.target_1, rec.levels.target_2)
print(rec.levels.risk_reward)
print(rec.verdict.thesis)            # list[str]
print(rec.verdict.summary)           # one-paragraph gist

# Serialize the whole thing (snapshot + levels + score + verdict) to JSON:
print(rec.model_dump_json(indent=2))
```

### Overriding settings

```python
from indi_analyst.config import Settings
from indi_analyst.analysis.engine import analyze

settings = Settings(
    default_llm_provider="ollama",
    atr_stop_multiple=2.0,   # wider stop
    target1_rr=1.5,
    history_period="2y",
)
rec = analyze("TCS", settings=settings)
```

### Injecting your own data source (offline / testing)

`analyze(...)` accepts `price_source` and `news_source`. Anything implementing the protocols in
`datasources/base.py` works — this is exactly how the tests run without a network:

```python
from indi_analyst.analysis.engine import analyze

rec = analyze(
    "TEST",
    provider="rulebased",
    price_source=my_price_source,   # implements resolve/history/fundamentals
    news_source=None,               # None -> skip news entirely
)
```

### Working with a snapshot directly

```python
from indi_analyst.analysis.snapshot import build_snapshot
from indi_analyst.analysis.engine import analyze_snapshot

snap = build_snapshot("HDFCBANK")           # deterministic: data + indicators only
rec = analyze_snapshot(snap, provider="ollama")
```

### Screening in code

```python
from indi_analyst.screener import scan_universe, apply, rank, resolve_preset

result = scan_universe("nifty50", provider="rulebased", limit=30)
for row in rank(apply(result.rows, resolve_preset("high-conviction-buys")), by="score"):
    print(row.symbol, row.action.value, row.score, f"{row.risk_reward:.1f}:1")
```

`scan_universe` accepts the same injectable `price_source` / `news_source` as `analyze`, so it
runs fully offline in tests (see `tests/test_screener.py`).

---

## Testing

```bash
uv run --extra dev pytest         # all tests, no network (mocked sources)
uv run --extra dev pytest -q
uv run --extra dev pytest tests/test_levels.py
```

Tests cover indicator correctness, trade-level invariants (stop below entry, ordered targets,
positive risk-reward), scoring behaviour (uptrends outscore downtrends), and an end-to-end engine
run against a synthetic source. See `tests/conftest.py` for the fixtures.
