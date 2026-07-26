"""Resolve a universe name to its constituents.

Resolution chain:
    fresh cache  ->  stale cache  ->  bundled data/<key>.csv  ->  bundled data/nifty50.csv

Any cache or bundled lookup can fail for an unknown index, and we degrade to the next available
local source, appending a human-readable warning rather than raising. Versioned constituent packs
(nifty50/200/500) ship inside the package under ``data/`` and are refreshed via
``scripts/refresh_universes.py`` — the analysis path never makes a live index request.

Also supports ad-hoc universes:
    watchlist:RELIANCE,TCS,INFY     inline comma-separated symbols
    file:/path/to/list.csv          a CSV (Symbol column) or newline-delimited symbol list
"""

from __future__ import annotations

import csv
import io
from importlib import resources
from pathlib import Path

from indi_analyst.config import Settings, get_settings
from indi_analyst.screener.cache import ScanCache
from indi_analyst.screener.models import Constituent

INDEX_UNIVERSES = {"nifty50", "nifty200", "nifty500"}


def _to_yf_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        return s
    return f"{s}.NS"


def _parse_constituent_csv(text: str) -> list[Constituent]:
    """Parse a constituent CSV (cols: Company Name, Industry, Symbol, ...)."""
    reader = csv.DictReader(io.StringIO(text))
    members: list[Constituent] = []
    for raw in reader:
        # Header names vary in whitespace/case across files — normalize keys.
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        symbol = row.get("symbol")
        if not symbol:
            continue
        members.append(
            Constituent(
                symbol=_to_yf_symbol(symbol),
                name=row.get("company name") or None,
                sector=row.get("industry") or None,
            )
        )
    return members


def _bundled(key: str) -> list[Constituent] | None:
    """Load a bundled constituent pack ``data/<key>.csv``, or None if no pack ships for it."""
    resource = resources.files("indi_analyst").joinpath(f"data/{key}.csv")
    if not resource.is_file():
        return None
    with resource.open("r", encoding="utf-8") as fh:
        return _parse_constituent_csv(fh.read())


def _load_from_path(path: str) -> list[Constituent]:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Universe file not found: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".csv" and "symbol" in text.splitlines()[0].lower():
        return _parse_constituent_csv(text)
    # Otherwise treat as a newline/comma-delimited symbol list.
    tokens = [t.strip() for line in text.splitlines() for t in line.split(",") if t.strip()]
    return [Constituent(symbol=_to_yf_symbol(t)) for t in tokens]


def load_universe(
    name: str,
    *,
    settings: Settings | None = None,
    cache: ScanCache | None = None,
    warnings: list[str] | None = None,
) -> list[Constituent]:
    """Resolve a universe name to cached, bundled, inline, or local constituents.

    `warnings`, if given, collects human-readable notes about any degradation.
    """
    settings = settings or get_settings()
    warn = warnings if warnings is not None else []
    key = name.strip().lower()

    # Ad-hoc universes never touch the cache/network.
    if key.startswith("watchlist:"):
        tokens = [t.strip() for t in name.split(":", 1)[1].split(",") if t.strip()]
        return [Constituent(symbol=_to_yf_symbol(t)) for t in tokens]
    if key.startswith("file:"):
        return _load_from_path(name.split(":", 1)[1])

    if key not in INDEX_UNIVERSES:
        raise ValueError(
            f"Unknown universe '{name}'. Use one of {sorted(INDEX_UNIVERSES)}, "
            "watchlist:SYM1,SYM2, or file:/path.csv."
        )

    cache = cache or ScanCache(settings.screener_cache_path)
    cached = cache.get_constituents(key, ttl_days=settings.universe_cache_ttl_days)

    # Fresh cache wins outright.
    if cached and cached[1]:
        return cached[0]

    # Stale cache beats the bundled snapshot (it is still this exact index).
    if cached:
        warn.append(f"Using stale cached constituents for {key}.")
        return cached[0]

    # Bundled pack for the requested index, if one ships.
    pack = _bundled(key)
    if pack is not None:
        return pack

    # No pack for this key — degrade to the always-present NIFTY 50 list.
    warn.append(f"No cached or bundled {key}; falling back to the bundled NIFTY 50 list.")
    fallback = _bundled("nifty50")
    assert fallback is not None  # nifty50.csv always ships
    return fallback
