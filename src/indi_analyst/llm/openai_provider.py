"""OpenAI-compatible provider — one adapter for OpenAI, Groq, OpenRouter, Together, etc.

They all share the OpenAI chat-completions shape, so a base_url override is enough.
"""

from __future__ import annotations

from indi_analyst.config import Settings
from indi_analyst.llm.base import ProviderError
from indi_analyst.llm.parsing import parse_verdict
from indi_analyst.llm.prompts import SYSTEM_PROMPT, serialize
from indi_analyst.models import AnalystVerdict, QuantScore, StockSnapshot, TradeLevels


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not set.")
        try:
            import openai
        except ImportError as e:
            raise ProviderError(
                "The 'openai' package is not installed. Install with: uv pip install 'indi-analyst[openai]'"
            ) from e
        self._openai = openai
        self.client = openai.OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,  # None -> official endpoint
            timeout=settings.llm_timeout,
        )
        self.model = settings.openai_model

    def verdict(
        self, snapshot: StockSnapshot, levels: TradeLevels, quant: QuantScore
    ) -> AnalystVerdict:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0.3,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": serialize(snapshot, levels, quant)},
                ],
            )
        except self._openai.OpenAIError as e:
            raise ProviderError(f"OpenAI-compatible API error: {e}") from e
        content = resp.choices[0].message.content or ""
        return parse_verdict(content)
