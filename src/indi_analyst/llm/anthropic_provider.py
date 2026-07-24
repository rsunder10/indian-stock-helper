"""Anthropic Claude provider — official SDK, structured output into AnalystVerdict."""

from __future__ import annotations

from indi_analyst.config import Settings
from indi_analyst.llm.base import ProviderError
from indi_analyst.llm.parsing import parse_verdict
from indi_analyst.llm.prompts import SYSTEM_PROMPT, serialize
from indi_analyst.models import AnalystVerdict, QuantScore, StockSnapshot, TradeLevels


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set.")
        try:
            import anthropic
        except ImportError as e:
            raise ProviderError(
                "The 'anthropic' package is not installed. Install with: uv pip install 'indi-analyst[anthropic]'"
            ) from e
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model
        self.timeout = settings.llm_timeout

    def verdict(
        self, snapshot: StockSnapshot, levels: TradeLevels, quant: QuantScore
    ) -> AnalystVerdict:
        user = serialize(snapshot, levels, quant)
        try:
            # messages.parse gives schema-validated structured output on Opus 4.8.
            resp = self.client.with_options(timeout=self.timeout).messages.parse(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": user}],
                output_format=AnalystVerdict,
            )
            if getattr(resp, "parsed_output", None) is not None:
                return resp.parsed_output
            # Fallback: pull the first text block and parse defensively.
            text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
            return parse_verdict(text)
        except self._anthropic.APIError as e:
            raise ProviderError(f"Anthropic API error: {e}") from e
