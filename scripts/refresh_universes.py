#!/usr/bin/env python3
"""Refresh the bundled NSE index constituent packs.

Opt-in, run manually — **never** on the analysis path. Downloads the free, no-key, static
constituent CSVs published by NSE Indices and writes them into the package's ``data/`` directory
so that ``nifty50`` / ``nifty200`` / ``nifty500`` universes resolve locally without a live
exchange request.

    uv run python scripts/refresh_universes.py

Source: https://niftyindices.com/IndexConstituent/ind_nifty<N>list.csv (public, no API key).
The files already carry the header we parse (``Company Name,Industry,Symbol``), so we write them
through verbatim after a sanity check on the row count.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import httpx

# key -> (source filename, rough expected constituent count for a sanity check)
PACKS = {
    "nifty50": ("ind_nifty50list.csv", 50),
    "nifty200": ("ind_nifty200list.csv", 200),
    "nifty500": ("ind_nifty500list.csv", 500),
}

_BASE = "https://niftyindices.com/IndexConstituent/{fname}"
# niftyindices.com rejects the default httpx UA; present a browser-like one.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
}

_DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "indi_analyst" / "data"


def _row_count(text: str) -> int:
    reader = csv.DictReader(io.StringIO(text))
    return sum(1 for row in reader if (row.get("Symbol") or "").strip())


def refresh() -> int:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    with httpx.Client(headers=_HEADERS, timeout=30.0, follow_redirects=True) as client:
        for key, (fname, expected) in PACKS.items():
            url = _BASE.format(fname=fname)
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001 — surface any fetch failure, keep going
                print(f"  ✗ {key}: fetch failed ({exc})", file=sys.stderr)
                failures += 1
                continue

            text = resp.text
            count = _row_count(text)
            if count < expected * 0.8:
                print(
                    f"  ✗ {key}: only {count} rows (expected ~{expected}); refusing to write",
                    file=sys.stderr,
                )
                failures += 1
                continue

            out = _DATA_DIR / f"{key}.csv"
            out.write_text(text, encoding="utf-8")
            print(f"  ✓ {key}: {count} constituents -> {out.relative_to(_DATA_DIR.parent.parent.parent)}")
    return failures


if __name__ == "__main__":
    print(f"Refreshing universe packs into {_DATA_DIR} ...")
    sys.exit(1 if refresh() else 0)
