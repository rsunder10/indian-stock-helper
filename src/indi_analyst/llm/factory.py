"""Select and construct an LLM provider from config, with rule-based fallback."""

from __future__ import annotations

from indi_analyst.config import Settings, get_settings
from indi_analyst.llm.base import LLMProvider, ProviderError
from indi_analyst.llm.rulebased import RuleBasedProvider


def build_provider(name: str | None = None, settings: Settings | None = None) -> LLMProvider:
    """Construct the named provider. Raises ProviderError if it can't be built."""
    settings = settings or get_settings()
    name = (name or settings.default_llm_provider).lower()

    if name == "rulebased":
        return RuleBasedProvider()
    if name == "ollama":
        from indi_analyst.llm.ollama_provider import OllamaProvider

        return OllamaProvider(settings)
    if name == "anthropic":
        from indi_analyst.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings)
    if name == "openai":
        from indi_analyst.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings)
    if name == "gemini":
        from indi_analyst.llm.gemini_provider import GeminiProvider

        return GeminiProvider(settings)

    raise ProviderError(f"Unknown provider '{name}'.")


def build_provider_with_fallback(
    name: str | None = None, settings: Settings | None = None
) -> tuple[LLMProvider, str | None]:
    """Build the requested provider; on failure fall back to rule-based.

    Returns (provider, note) where note is a human-readable fallback reason (or None).
    """
    settings = settings or get_settings()
    requested = (name or settings.default_llm_provider).lower()
    if requested == "rulebased":
        return RuleBasedProvider(), None
    try:
        return build_provider(requested, settings), None
    except ProviderError as e:
        return RuleBasedProvider(), f"'{requested}' unavailable ({e}); using rule-based analysis."
