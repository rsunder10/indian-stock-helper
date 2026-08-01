# LLM providers & configuration

`indi-analyst` is provider-agnostic. The deterministic engine produces the snapshot, levels, and
score; a provider only writes the **analyst verdict** (thesis, risks, catalysts, summary) over
that snapshot. Every provider implements one method — `verdict(snapshot, levels, quant, valuation) ->
AnalystVerdict` — so they're fully interchangeable.

If the selected provider can't be built (missing package, no key) or fails at request time, the
tool **automatically falls back to the rule-based provider** and notes it as a warning. You never
get a hard failure just because a model is unavailable.

## Selecting a provider

- Globally: set `DEFAULT_LLM_PROVIDER` in `.env`.
- Per run: `--provider` on the CLI, or the sidebar selector in the dashboard.

Valid values: `ollama` · `anthropic` · `openai` · `gemini` · `rulebased`.

---

## The providers

### `ollama` — local, default (no key)

Runs a local model via [Ollama](https://ollama.com). Zero cost, private, no API key. Best if you
have (or don't mind installing) Ollama; quality scales with model size and your hardware.

```bash
ollama serve &
ollama pull llama3.1        # or qwen2.5, mistral, etc.
```

```dotenv
DEFAULT_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

Uses Ollama's `/api/chat` with JSON mode. Only needs `httpx` (already a base dependency).

### `anthropic` — Claude (highest quality)

```bash
uv sync --extra anthropic
```

```dotenv
DEFAULT_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-8
```

Uses the official `anthropic` SDK with structured output (schema-validated straight into
`AnalystVerdict`) and adaptive thinking.

### `openai` — OpenAI-compatible (OpenAI / Groq / OpenRouter / Together …)

One adapter covers any OpenAI-chat-compatible endpoint — just point `OPENAI_BASE_URL` at it.

```bash
uv sync --extra openai
```

```dotenv
DEFAULT_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=https://api.groq.com/openai/v1   # omit for official OpenAI
OPENAI_MODEL=gpt-4o-mini
```

Requests JSON output via `response_format={"type": "json_object"}`.

### `gemini` — Google Gemini (has a free tier)

```bash
uv sync --extra gemini
```

```dotenv
DEFAULT_LLM_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-1.5-flash
```

Uses `google-generativeai` with a JSON response MIME type.

### `rulebased` — no LLM

Synthesizes the verdict deterministically from the quant score and levels — the strongest
scoring reasons become the thesis, with derived risks/catalysts and a templated gist. Needs no
setup and is also the universal fallback. Great for CI, offline use, or when you just want the
numbers.

```dotenv
DEFAULT_LLM_PROVIDER=rulebased
```

---

## All settings (`.env`)

Loaded by `config.py` via pydantic-settings; environment variables override `.env`.

| Key | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_LLM_PROVIDER` | `ollama` | Which provider writes the verdict |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.1` | Ollama model tag |
| `ANTHROPIC_API_KEY` | — | Claude key |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | Claude model |
| `OPENAI_API_KEY` | — | OpenAI-compatible key |
| `OPENAI_BASE_URL` | — (official OpenAI) | Override for Groq/OpenRouter/etc. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model name |
| `GEMINI_API_KEY` | — | Gemini key |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Gemini model |
| `ATR_STOP_MULTIPLE` | `1.8` | Stop = entry − k·ATR |
| `TARGET1_RR` | `2.0` | Target 1 at N×risk |
| `TARGET2_RR` | `3.5` | Target 2 at N×risk |
| `HISTORY_PERIOD` | `1y` | yfinance history window for indicators |
| `NEWS_MAX_ITEMS` | `8` | Headlines fetched |
| `NEWS_RECENCY_HALFLIFE_DAYS` | `7.0` | Sentiment weight halves every N days (0 = flat mean) |
| `LLM_TIMEOUT` | `90.0` | Per-request timeout (seconds) |
| `YF_MAX_RETRIES` | `3` | Attempts per yfinance call before giving up |
| `YF_RETRY_BACKOFF` | `0.5` | Base seconds for exponential backoff between retries |
| `YF_MIN_REQUEST_INTERVAL` | `0.15` | Min seconds between yfinance calls (0 = no throttle) |

Macro-pack settings are also available through `.env`: `BUDGET_YEAR` (default `2026-27`),
`RATE_PACK_VERSION`, and the six `<KIND>_PACK_VERSION` / `<KIND>_ENABLED` pairs. The bundled
national-indicator packs are seed values until refreshed with `scripts/refresh_macro.py`; their
CLI/dashboard status and snapshot warnings expose that state.

---

## Adding a new provider

1. Create `src/indi_analyst/llm/<name>_provider.py` with a class exposing `name` and
   `verdict(self, snapshot, levels, quant, valuation) -> AnalystVerdict`.
2. Reuse `prompts.SYSTEM_PROMPT` + `prompts.serialize(...)` for the request, and
   `parsing.parse_verdict(...)` to validate the model's JSON.
3. Register it in `llm/factory.py` (and add any key to `config.py` +
   `Settings.configured_providers()` so the dashboard lists it).

That's the entire contract — the engine, UI, and CLI need no changes.
