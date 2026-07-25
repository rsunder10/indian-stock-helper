# indi-analyst documentation

Docs for the Indian (NSE/BSE) stock analysis tool. Start with the [project README](../README.md)
for the quickstart.

| Doc | What's in it |
| --- | --- |
| [architecture.md](architecture.md) | Layered design, data flow, module map, and extension points |
| [usage.md](usage.md) | Dashboard, CLI, and using the engine as a Python library |
| [providers.md](providers.md) | Configuring each LLM provider + every `.env` setting |
| [methodology.md](methodology.md) | How indicators, trade levels, and the quant score are computed |
| [roadmap.md](roadmap.md) | Where the project is going (free-source hardening, backtesting…) |

## TL;DR

`indi-analyst` computes a stock's technical + fundamental + sentiment picture **deterministically**
(so you always get concrete buy/target/stop numbers), then a **swappable LLM provider** — local
Ollama by default, or Claude / OpenAI-compatible / Gemini, or a no-LLM rule-based engine — writes
the analyst narrative. It never depends on a single model and degrades gracefully to rule-based
analysis.
