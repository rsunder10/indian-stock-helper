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

    # --- Macro overlays (sector-keyed government-open-data signals) ---
    # Small, bounded, explainable nudges to the composite score from bundled, build-time-refreshed
    # packs (budget, RBI rate cycle, …). The analysis path is fully offline. See
    # docs/methodology.md ("Macro overlays"). `macro_max_points` caps the COMBINED nudge across all
    # overlays so they stay small vs. the technical/fundamental core.
    macro_max_points: float = 6.0  # cap on the combined (signed) nudge across all overlays

    # Budget overlay (bundled Union-Budget pack; build-time fetch: scripts/refresh_budget.py)
    budget_enabled: bool = True  # master switch for the budget overlay
    budget_year: str = "2023-24"  # selects the bundled data/budget_<year>.json pack (real data.gov.in data)
    budget_data_path: str | None = None  # optional override path to a pack JSON; else the bundled one
    budget_max_points: float = 5.0  # per-source cap on the budget nudge
    budget_yoy_scale: float = 20.0  # allocation YoY% that maps to a full (+/-1) tailwind
    budget_api_key: str | None = None  # free data.gov.in OGD key — build-time fetch only (shared)

    # RBI rate-cycle overlay (bundled pack; build-time fetch: scripts/refresh_rates.py)
    rate_enabled: bool = True  # master switch for the rate-cycle overlay
    rate_pack_version: str = "2026"  # selects the bundled data/rates_<version>.json pack
    rate_data_path: str | None = None  # optional override path to a pack JSON; else the bundled one
    rate_max_points: float = 4.0  # per-source cap on the rate-cycle nudge

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
