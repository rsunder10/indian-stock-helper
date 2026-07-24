"""The single protocol every LLM backend implements."""

from __future__ import annotations

from typing import Protocol

from indi_analyst.models import AnalystVerdict, QuantScore, StockSnapshot, TradeLevels


class LLMProvider(Protocol):
    name: str

    def verdict(
        self, snapshot: StockSnapshot, levels: TradeLevels, quant: QuantScore
    ) -> AnalystVerdict:
        """Turn a deterministic snapshot + levels + quant score into an analyst verdict."""
        ...


class ProviderError(RuntimeError):
    """Raised when a provider can't run (missing dep, no key, unreachable, bad output)."""
