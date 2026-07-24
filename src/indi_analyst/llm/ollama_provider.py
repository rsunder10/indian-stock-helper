"""Local LLM via Ollama's REST API. Default provider — zero cost, no key."""

from __future__ import annotations

import httpx

from indi_analyst.config import Settings
from indi_analyst.llm.base import ProviderError
from indi_analyst.llm.parsing import parse_verdict
from indi_analyst.llm.prompts import SYSTEM_PROMPT, serialize
from indi_analyst.models import AnalystVerdict, QuantScore, StockSnapshot, TradeLevels


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings):
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = settings.llm_timeout

    def verdict(
        self, snapshot: StockSnapshot, levels: TradeLevels, quant: QuantScore
    ) -> AnalystVerdict:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",  # Ollama JSON mode
            "options": {"temperature": 0.3},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": serialize(snapshot, levels, quant)},
            ],
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
        except httpx.ConnectError as e:
            raise ProviderError(
                f"Cannot reach Ollama at {self.base_url}. Is it running? "
                f"(`ollama serve` / `ollama run {self.model}`)"
            ) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"Ollama request failed: {e}") from e

        content = resp.json().get("message", {}).get("content", "")
        if not content:
            raise ProviderError("Ollama returned an empty response.")
        return parse_verdict(content)
