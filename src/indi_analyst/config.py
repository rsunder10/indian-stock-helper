"""Runtime configuration via environment / .env (pydantic-settings)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM provider selection ---
    # one of: ollama | anthropic | openai | gemini | rulebased
    default_llm_provider: str = "ollama"

    # Ollama (local, default — no key needed)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # Anthropic
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-8"

    # OpenAI-compatible (OpenAI / Groq / OpenRouter / Together ...)
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # None -> official OpenAI endpoint
    openai_model: str = "gpt-4o-mini"

    # Google Gemini
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-flash"

    # --- Analysis tunables ---
    atr_stop_multiple: float = 1.8  # stop = entry - k * ATR
    target1_rr: float = 2.0  # target 1 at 2R
    target2_rr: float = 3.5  # target 2 at 3.5R
    history_period: str = "1y"  # yfinance period for indicator computation
    news_max_items: int = 8

    # LLM request timeout (seconds)
    llm_timeout: float = 90.0

    def configured_providers(self) -> list[str]:
        """Providers that have what they need to run (key present, etc.)."""
        providers = ["rulebased", "ollama"]  # always available (ollama may still fail to connect)
        if self.anthropic_api_key:
            providers.append("anthropic")
        if self.openai_api_key:
            providers.append("openai")
        if self.gemini_api_key:
            providers.append("gemini")
        return providers


def get_settings() -> Settings:
    return Settings()
