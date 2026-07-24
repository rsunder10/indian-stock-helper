"""Google Gemini provider."""

from __future__ import annotations

from indi_analyst.config import Settings
from indi_analyst.llm.base import ProviderError
from indi_analyst.llm.parsing import parse_verdict
from indi_analyst.llm.prompts import SYSTEM_PROMPT, serialize
from indi_analyst.models import AnalystVerdict, QuantScore, StockSnapshot, TradeLevels


class GeminiProvider:
    name = "gemini"

    def __init__(self, settings: Settings):
        if not settings.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY is not set.")
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise ProviderError(
                "The 'google-generativeai' package is not installed. "
                "Install with: uv pip install 'indi-analyst[gemini]'"
            ) from e
        genai.configure(api_key=settings.gemini_api_key)
        self._genai = genai
        self.model = genai.GenerativeModel(
            settings.gemini_model,
            system_instruction=SYSTEM_PROMPT,
            generation_config={"temperature": 0.3, "response_mime_type": "application/json"},
        )
        self.timeout = settings.llm_timeout

    def verdict(
        self, snapshot: StockSnapshot, levels: TradeLevels, quant: QuantScore
    ) -> AnalystVerdict:
        try:
            resp = self.model.generate_content(
                serialize(snapshot, levels, quant),
                request_options={"timeout": self.timeout},
            )
        except Exception as e:
            raise ProviderError(f"Gemini API error: {e}") from e
        return parse_verdict(resp.text or "")
