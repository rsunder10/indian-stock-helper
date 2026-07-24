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

---

## As a Python library

The engine returns a plain `Recommendation` pydantic model — ideal for scripts, notebooks, or a
future screener/API.

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
