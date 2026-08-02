#!/usr/bin/env python3
"""Refresh a bundled national-indicator overlay pack (IIP, GST, credit, trade, input-cost, monsoon).

Opt-in, run manually — **never** on the analysis path (the runtime reads only the bundled
`src/indi_analyst/data/<kind>_<version>.json`). Each of these overlays is a single national headline
number × a maintained sector-sensitivity crosswalk (see analysis/overlays.py). This script updates ONLY
that one number (`value`) plus `as_of`/`fetched_at`; the `sector_sensitivity` crosswalk and `neutral`
baseline are maintained config and are never touched by a fetch — so a raw feed can never silently
re-weight the sectors.

Two ways to set the headline value:

  1. **Directly** (quickest — read the latest figure off the PIB / RBI / IMD release):

        uv run python scripts/refresh_macro.py --kind gst --value 11.2 --as-of 2026-06

  2. **From the free data.gov.in OGD API** (register a key at https://data.gov.in, put it in .env as
     BUDGET_API_KEY — the OGD key is shared across packs):

        uv run python scripts/refresh_macro.py --kind iip --resource <ogd-resource-id> --field growth_rate

The `--kind` must be one of the registered generic overlays. Validates before writing; leaves the pack
unchanged on any failure. Same free-source-first tradeoff as the universe/budget/rates packs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import httpx

from indi_analyst.analysis.overlays import SENSITIVITY_OVERLAYS
from indi_analyst.config import get_settings

_DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "indi_analyst" / "data"
_OGD_BASE = "https://api.data.gov.in/resource/{resource}"
# data.gov.in's WAF stalls requests without a browser-like User-Agent (see refresh_budget.py).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json,*/*",
}
_KINDS = {spec.kind for spec in SENSITIVITY_OVERLAYS}


def _fetch_latest_value(resource: str, api_key: str, field: str) -> float | None:
    """Return the most recent numeric value of `field` from an OGD resource, or None."""
    url = _OGD_BASE.format(resource=resource)
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=_HEADERS) as client:
        resp = client.get(url, params={"api-key": api_key, "format": "json", "limit": "1000"})
        resp.raise_for_status()
        records = resp.json().get("records", [])
    for rec in reversed(records):  # OGD returns oldest-first; take the last parseable value
        try:
            return round(float(rec[field]), 1)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def refresh(args: argparse.Namespace) -> int:
    if args.kind not in _KINDS:
        print(
            f"  ✗ unknown --kind {args.kind!r}; expected one of {sorted(_KINDS)}", file=sys.stderr
        )
        return 1

    pack_path = _DATA_DIR / f"{args.kind}_{args.version}.json"
    if not pack_path.is_file():
        print(
            f"  ✗ no bundled pack at {pack_path}; create the sensitivity skeleton first",
            file=sys.stderr,
        )
        return 1
    pack = json.loads(pack_path.read_text(encoding="utf-8"))

    value: float | None = args.value
    if args.resource:
        api_key = get_settings().budget_api_key  # from env or the gitignored .env
        if not api_key:
            print(
                "  ! --resource given but no BUDGET_API_KEY in env; skipping fetch", file=sys.stderr
            )
        else:
            try:
                value = _fetch_latest_value(args.resource, api_key, args.field)
            except Exception as exc:
                print(f"  ✗ fetch failed ({exc}); pack left unchanged", file=sys.stderr)
                return 1
            if value is None:
                print(
                    "  ✗ fetch returned no parseable value; check --field / resource",
                    file=sys.stderr,
                )
                return 1

    if value is None:
        print("  ! nothing to change — pass --value or --resource.", file=sys.stderr)
        return 1

    old = pack.get("value")
    pack["value"] = value
    if args.neutral is not None:
        pack["neutral"] = args.neutral
    if args.as_of:
        pack["as_of"] = args.as_of
    pack["fetched_at"] = date.today().isoformat()

    if args.dry_run:
        print(f"  (dry-run) {args.kind}: value {old} → {value}; not written.")
        return 0

    pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  ✓ {args.kind} {args.version}: value {old} → {value} -> {pack_path.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh a bundled national-indicator overlay pack.")
    ap.add_argument("--kind", required=True, help=f"overlay kind: {sorted(_KINDS)}")
    ap.add_argument(
        "--version", default="2026", help="Pack version selecting data/<kind>_<version>.json"
    )
    ap.add_argument("--value", type=float, default=None, help="set the headline number directly")
    ap.add_argument(
        "--neutral", type=float, default=None, help="optionally update the zero-nudge baseline"
    )
    ap.add_argument("--as-of", default=None, help="period label the number covers, e.g. 2026-06")
    ap.add_argument(
        "--resource", default=None, help="data.gov.in OGD resource id to fetch the value from"
    )
    ap.add_argument("--field", default="value", help="record field holding the headline number")
    ap.add_argument("--dry-run", action="store_true", help="preview, but do not write the pack")
    args = ap.parse_args()
    print(f"Refreshing {args.kind} pack {args.version} into {_DATA_DIR} ...")
    return refresh(args)


if __name__ == "__main__":
    raise SystemExit(main())
