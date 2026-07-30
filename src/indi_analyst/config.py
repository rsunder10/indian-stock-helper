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
    news_recency_halflife_days: float = 7.0  # sentiment weight halves every N days (0 = flat mean)

    # --- Fair-value (intrinsic value) tunables ---
    fair_value_discount_rate: float = 0.12  # cost of equity `r` for the Gordon DDM
    fair_value_terminal_growth: float = 0.05  # cap on dividend growth `g` (must stay < r)
    fair_pe_base: float = 15.0  # fair P/E used when growth is unknown
    fair_pe_floor: float = 10.0  # lowest justified P/E
    fair_pe_cap: float = 35.0  # highest justified P/E (avoids paying any multiple)
    margin_of_safety: float = 0.15  # +/- band that flips under/fairly/over-valued

    # --- Corporate actions (free dividend/split history) ---
    corporate_action_lookback_years: int = 6  # window for counting dividend-paying years
    dividend_min_consistent_years: int = 3  # min paying years in the window to trust the DDM
    split_recency_days: int = 365  # a split this recent flags recent_split / a snapshot warning

    # LLM request timeout (seconds)
    llm_timeout: float = 90.0

    # --- Free-source hardening (yfinance rate/retry discipline) ---
    yf_max_retries: int = 3  # attempts per yfinance network call before giving up
    yf_retry_backoff: float = 0.5  # base seconds for exponential backoff between retries
    yf_min_request_interval: float = 0.15  # min seconds between yfinance calls (0 = no throttle)

    # --- Screener / batch-scan tunables ---
    screener_cache_path: str = "~/.cache/indi-analyst/screener.db"
    screener_max_workers: int = 8  # concurrent per-symbol scans (I/O-bound)
    snapshot_cache_ttl_hours: float = 12  # reuse a cached snapshot within this window
    universe_cache_ttl_days: float = 7  # refetch index constituents after this

    # --- Backtesting (walk-forward replay of the deterministic pipeline) ---
    # Fundamentals/news are NOT point-in-time available from the free source, so the backtest is
    # deliberately technical-only (no look-ahead). See docs/methodology.md ("Backtesting").
    backtest_history_period: str = "5y"  # yfinance period fetched per symbol for the replay
    backtest_warmup_bars: int = 200  # bars required before the first signal (SMA-200 needs them)
    backtest_max_hold_bars: int = 40  # force an exit at close after this many bars in a trade
    backtest_entry_actions: str = "BUY,ACCUMULATE"  # quant actions that open a simulated long

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
