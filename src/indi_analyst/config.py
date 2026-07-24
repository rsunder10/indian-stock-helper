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

    # --- Data / price source selection ---
    # one of: yfinance (default) | nse (NSE-direct live quote overlaid on yfinance history)
    default_price_source: str = "yfinance"
    nse_quote_base_url: str = "https://www.nseindia.com"
    nse_quote_timeout: float = 10.0  # per-request timeout for the NSE quote API
    nse_quote_max_concurrency: int = 4  # cap simultaneous NSE hits (screener fan-out)

    # LLM request timeout (seconds)
    llm_timeout: float = 90.0

    # --- Screener / batch-scan tunables ---
    screener_cache_path: str = "~/.cache/indi-analyst/screener.db"
    screener_max_workers: int = 8  # concurrent per-symbol scans (I/O-bound)
    snapshot_cache_ttl_hours: float = 12  # reuse a cached snapshot within this window
    universe_cache_ttl_days: float = 7  # refetch index constituents after this
    nse_indices_base_url: str = "https://nsearchives.nseindia.com/content/indices"

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
