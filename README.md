# indi-analyst 📈

Investment-banking-grade analysis for **Indian (NSE/BSE)** stocks. Give it a ticker and it
pulls data from free sources, computes the full technical + fundamental + sentiment picture,
and returns an actionable call — **when to buy, target price(s), and stop-loss** — with a
facts-based "why to invest" thesis.

- **Deterministic first.** Every indicator, trade level, and score is computed in Python
  before any LLM runs, so you always get concrete numbers — even fully offline.
- **Provider-agnostic brain.** A thin, swappable LLM layer writes the analyst narrative.
  Runs on a **local model (Ollama, default — no key)**, or Claude / OpenAI-compatible / Gemini,
  or a **rule-based** engine with no LLM at all.
- **Never a hard dependency on one model.** If a provider is missing or unreachable, the tool
  transparently falls back to rule-based analysis.

> ⚠️ For research and educational purposes only. **Not investment advice.**

---

## Quickstart

Requires [`uv`](https://docs.astral.sh/uv/) and Python ≥ 3.11.

```bash
uv sync                      # base install — Ollama + rule-based work out of the box
cp .env.example .env         # optional: pick a provider / add a key

# (optional) local LLM — no API key needed:
ollama serve & ollama pull llama3.1
```

**Web dashboard:**

```bash
uv run streamlit run src/indi_analyst/app/dashboard.py
```

**CLI:**

```bash
uv run indi-analyst RELIANCE
uv run indi-analyst TCS --provider rulebased
uv run indi-analyst INFY.NS --provider ollama
```

**Tests:**

```bash
uv run --extra dev pytest        # unit + end-to-end, no network (mocked data sources)
```

---

## How it works

```
ticker
  → data (yfinance OHLCV + fundamentals, Google News RSS + VADER sentiment)
  → technical indicators (RSI, MACD, SMAs, Bollinger, ADX, ATR, S/R, trend)
  → StockSnapshot  ── the single deterministic source of truth
  → trade levels (ATR stop + risk-reward targets)  +  quant score (technical + fundamental)
  → LLMProvider.verdict()  ── narrative, risks, catalysts (or rule-based)
  → Recommendation  ── rendered in the dashboard / CLI
```

Full write-up in **[docs/architecture.md](docs/architecture.md)**.

---

## Documentation

| Doc | What's in it |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Layered design, data flow, module map, extension points |
| [docs/usage.md](docs/usage.md) | Dashboard, CLI, and using the engine as a Python library |
| [docs/providers.md](docs/providers.md) | Configuring each LLM provider + all `.env` settings |
| [docs/methodology.md](docs/methodology.md) | How indicators, trade levels, and the quant score are computed |
| [docs/roadmap.md](docs/roadmap.md) | Where this is going (screener, real-time data, backtesting, …) |

---

## Providers at a glance

| Provider | Key needed | Notes |
| --- | --- | --- |
| `ollama` (default) | No | Local models (Llama 3, Qwen, Mistral…). Private, zero cost. |
| `anthropic` | Yes | Claude Opus 4.8 — highest-quality narrative. `uv sync --extra anthropic` |
| `openai` | Yes | OpenAI / Groq / OpenRouter / Together (one adapter). `uv sync --extra openai` |
| `gemini` | Yes | Google Gemini (has a free tier). `uv sync --extra gemini` |
| `rulebased` | No | No LLM at all; also the automatic fallback. |

Select with `DEFAULT_LLM_PROVIDER` in `.env` or `--provider` on the CLI. See
[docs/providers.md](docs/providers.md).

---

## Project layout

```
src/indi_analyst/
├── config.py            # settings via .env (pydantic-settings)
├── models.py            # StockSnapshot, TradeLevels, QuantScore, AnalystVerdict, Recommendation
├── datasources/         # yfinance (prices+fundamentals), Google News RSS; source protocols
├── indicators/          # technical.py — RSI/MACD/SMA/Bollinger/ADX/ATR/S-R (pure pandas/numpy)
├── analysis/            # snapshot, levels, scoring, engine
├── llm/                 # base protocol, prompts, factory, + ollama/anthropic/openai/gemini/rulebased
├── app/dashboard.py     # Streamlit UI
└── cli.py               # `indi-analyst <ticker>`
```

---

## Roadmap (short version)

- **Next:** multi-stock **screener / recommender** — rank a universe and surface buy candidates.
- Real-time NSE quotes; report export (PDF/Markdown); watchlists & alerts.
- FastAPI + JS frontend; backtesting the level logic; more data sources.

Full plan with milestones in **[docs/roadmap.md](docs/roadmap.md)**.

---

## License

TBD.
