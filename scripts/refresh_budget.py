#!/usr/bin/env python3
"""Refresh the bundled Union-Budget sector pack from a free Open-Government source.

Opt-in, run manually — **never** on the analysis path (the runtime reads only the bundled
`src/indi_analyst/data/budget_<year>.json`). This fetches machine-readable budget allocations from
the **data.gov.in OGD API** (free, register for a key at https://data.gov.in) and refreshes the
``heads`` block (allocation + YoY) of the pack. The two maintained pieces are left untouched: the
``sector_map`` crosswalk (sector -> budget head) and the tailwind transform live in code/config, so
a raw feed can never silently distort the signal.

    # key in .env as BUDGET_API_KEY (or export it), then:
    uv run python scripts/refresh_budget.py --year 2026-27 --resource <ogd-resource-id>
    uv run python scripts/refresh_budget.py --year 2026-27 --resource <id> --dry-run   # preview only

Because each year's dataset uses its own resource id and column names, the field mapping is
overridable on the CLI (``--field-head`` / ``--field-alloc`` / ``--field-prev``). Robust by design
(mirrors scripts/refresh_universes.py): paginates, retries with backoff, validates before writing,
and leaves the last good pack in place on any failure. This is the same free-source-first tradeoff
already used for the NSE universe packs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from indi_analyst.config import get_settings

_DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "indi_analyst" / "data"
_OGD_BASE = "https://api.data.gov.in/resource/{resource}"
_PAGE = 1000  # OGD max page size (most budget datasets fit in a single request)
# The data.gov.in WAF stalls requests that lack a browser-like User-Agent (same quirk
# scripts/refresh_universes.py hits with niftyindices.com), so present one.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json,*/*",
}


def _get(client: httpx.Client, url: str, params: dict, *, retries: int = 3, backoff: float = 0.8):
    """GET with simple exponential-backoff retries (transient 5xx / network blips)."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last = exc
            time.sleep(backoff * 2**attempt)
    raise last  # type: ignore[misc]


def _num(v) -> float | None:
    """Parse an OGD numeric cell (may be an int, float, or a string with commas)."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _fetch_rows(
    resource: str, api_key: str, fields: dict[str, str], row_filter: tuple[str, str] | None
) -> dict[str, dict]:
    """Page through the OGD resource and fold records into a raw {head-name: {alloc, prev}} map.

    Government budget datasets are typically row-per-scheme with a per-head "Total" row; pass a
    ``row_filter`` (field, value) — e.g. ("scheme", "Total") — to keep just those totals. Numbers
    are summed per head name so a dataset without explicit totals still aggregates correctly.
    """
    url = _OGD_BASE.format(resource=resource)
    heads: dict[str, dict] = {}
    offset = 0
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=_HEADERS) as client:
        while True:
            data = _get(
                client,
                url,
                {
                    "api-key": api_key,
                    "format": "json",
                    "limit": str(_PAGE),
                    "offset": str(offset),
                },
            )
            records = data.get("records", [])
            if not records:
                break
            for rec in records:
                if (
                    row_filter
                    and (rec.get(row_filter[0]) or "").strip().lower()
                    != row_filter[1].strip().lower()
                ):
                    continue
                name = (rec.get(fields["head"]) or "").strip()
                alloc, prev = _num(rec.get(fields["alloc"])), _num(rec.get(fields["prev"]))
                if not name or alloc is None or prev is None:
                    continue
                acc = heads.setdefault(name, {"alloc_cr": 0.0, "prev_cr": 0.0})
                acc["alloc_cr"] += alloc
                acc["prev_cr"] += prev
            offset += _PAGE
            if len(records) < _PAGE:
                break
    return heads


def _match_head(referenced_head: str, fetched_name: str) -> bool:
    """Forgiving match: the short crosswalk head vs the dataset's full ministry/department name."""
    a, b = referenced_head.strip().lower(), fetched_name.strip().lower()
    return a == b or a in b or b in a


def refresh(year, resource, api_key, fields, row_filter, dry_run) -> int:
    pack_path = _DATA_DIR / f"budget_{year}.json"
    if not pack_path.is_file():
        print(
            f"  ✗ no bundled pack at {pack_path}; create the crosswalk skeleton first",
            file=sys.stderr,
        )
        return 1
    pack = json.loads(pack_path.read_text(encoding="utf-8"))

    if not resource or not api_key:
        print(
            "  ! no --resource / BUDGET_API_KEY given — nothing fetched. Register a free key at\n"
            "    https://data.gov.in and pass a Union-Budget expenditure resource id to refresh the\n"
            "    `heads` numbers. The bundled pack (sector_map + last figures) is left unchanged.",
            file=sys.stderr,
        )
        return 1

    try:
        fetched = _fetch_rows(resource, api_key, fields, row_filter)
    except Exception as exc:
        print(f"  ✗ fetch failed ({exc}); pack left unchanged", file=sys.stderr)
        return 1
    if not fetched:
        print(
            "  ✗ fetch returned no usable rows (check --resource / --field-* / --filter-*)",
            file=sys.stderr,
        )
        return 1

    # Match the crosswalk's short heads (Defence, Railways, …) against the dataset's full names
    # (Capital Outlay on Defence Services, …). A maintained `head_aliases` map in the pack pins
    # each head to the exact dataset name fragment(s); heads without an alias fall back to a
    # forgiving substring match. Only refresh referenced heads; never drop one the sector_map
    # depends on just because the feed omitted it.
    aliases_map = pack.get("head_aliases", {})
    referenced = {h for heads in pack["sector_map"].values() for h in heads}
    updated: dict[str, dict] = {}
    for head in referenced:
        aliases = aliases_map.get(head)  # None -> fall back to fuzzy; a list -> authoritative
        for name, agg in fetched.items():
            if aliases is not None:
                hit = any(a.strip().lower() in name.strip().lower() for a in aliases)
            else:
                hit = _match_head(head, name)
            if hit and agg["prev_cr"]:
                yoy = round((agg["alloc_cr"] - agg["prev_cr"]) / agg["prev_cr"] * 100, 1)
                updated[head] = {
                    "alloc_cr": round(agg["alloc_cr"], 0),
                    "yoy_pct": yoy,
                    "_src": name,
                }
                break

    for head, vals in sorted(updated.items()):
        print(
            f"    · {head}  ←  {vals['_src']}: ₹{vals['alloc_cr']:,.0f} cr ({vals['yoy_pct']:+.1f}% YoY)"
        )
    print(
        f"  matched {len(updated)}/{len(referenced)} referenced heads from {len(fetched)} fetched rows."
    )

    if dry_run:
        print("  (dry-run) not written.")
        return 0
    if len(updated) < max(1, len(referenced) // 2):
        print(
            f"  ✗ only {len(updated)}/{len(referenced)} heads matched; refusing to write "
            "(align the crosswalk head names or the --field-*/--filter-* mapping)",
            file=sys.stderr,
        )
        return 1

    for head, vals in updated.items():
        pack["heads"].setdefault(head, {}).update(
            {"alloc_cr": vals["alloc_cr"], "yoy_pct": vals["yoy_pct"]}
        )
    pack["fetched_at"] = date.today().isoformat()
    pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"  ✓ {year}: refreshed {len(updated)} head(s) -> {pack_path.relative_to(_DATA_DIR.parent.parent.parent)}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh the bundled Union-Budget sector pack.")
    ap.add_argument(
        "--year", default="2026-27", help="Budget year selecting data/budget_<year>.json"
    )
    ap.add_argument(
        "--resource", default=None, help="data.gov.in OGD resource id for the expenditure dataset"
    )
    ap.add_argument(
        "--field-head",
        default="department_or_ministry",
        help="record field: budget head / ministry name",
    )
    ap.add_argument(
        "--field-alloc",
        default="budget_estimates_current",
        help="record field: current-year BE (₹ crore)",
    )
    ap.add_argument(
        "--field-prev",
        default="budget_estimates_previous",
        help="record field: previous-year BE (₹ crore)",
    )
    ap.add_argument(
        "--filter-field",
        default=None,
        help="keep only rows where this field == --filter-value (e.g. scheme)",
    )
    ap.add_argument(
        "--filter-value",
        default=None,
        help="value for --filter-field (e.g. Total, for per-head totals)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="fetch and preview, but do not write the pack"
    )
    args = ap.parse_args()
    fields = {"head": args.field_head, "alloc": args.field_alloc, "prev": args.field_prev}
    row_filter = (
        (args.filter_field, args.filter_value) if args.filter_field and args.filter_value else None
    )
    # Key comes from Settings, which reads BUDGET_API_KEY from the environment or the gitignored .env.
    api_key = get_settings().budget_api_key
    print(f"Refreshing budget pack {args.year} into {_DATA_DIR} ...")
    return refresh(args.year, args.resource, api_key, fields, row_filter, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
