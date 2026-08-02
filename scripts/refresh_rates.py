#!/usr/bin/env python3
"""Refresh the bundled RBI rate-cycle pack.

Opt-in, run manually — **never** on the analysis path (the runtime reads only the bundled
`src/indi_analyst/data/rates_<version>.json`). Two ways to keep it current:

  1. **After each RBI MPC** (the repo rate / stance move a handful of times a year) set them directly:

        uv run python scripts/refresh_rates.py --repo-rate 5.75 --prev-repo-rate 6.00 --stance accommodative

  2. **Refresh CPI** from the free data.gov.in OGD API (register a key at https://data.gov.in, put it
     in .env as BUDGET_API_KEY — the OGD key is shared) so the inflation context stays fresh:

        uv run python scripts/refresh_rates.py --cpi-resource <ogd-resource-id> --field-cpi rate

The maintained `sector_sensitivity` crosswalk (how rate-sensitive each sector is) is never touched by
a fetch — only the small set of headline numbers is. `regime` is derived by the runtime from repo vs
prev_repo, so it is recomputed automatically. Validates before writing; leaves the pack unchanged on
any failure. Same free-source-first tradeoff as the universe/budget packs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import httpx

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


def _fetch_latest_cpi(resource: str, api_key: str, field: str) -> float | None:
    """Return the most recent CPI YoY value from an OGD resource, or None."""
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
    pack_path = _DATA_DIR / f"rates_{args.version}.json"
    if not pack_path.is_file():
        print(
            f"  ✗ no bundled pack at {pack_path}; create the sensitivity skeleton first",
            file=sys.stderr,
        )
        return 1
    pack = json.loads(pack_path.read_text(encoding="utf-8"))

    changed: list[str] = []
    if args.repo_rate is not None:
        pack["prev_repo_rate"] = (
            args.prev_repo_rate if args.prev_repo_rate is not None else pack.get("repo_rate")
        )
        pack["repo_rate"] = args.repo_rate
        changed.append(f"repo {pack['prev_repo_rate']}→{args.repo_rate}%")
    if args.stance:
        pack["stance"] = args.stance
        changed.append(f"stance={args.stance}")
    if args.cpi is not None:
        pack["cpi_yoy"] = args.cpi
        changed.append(f"CPI={args.cpi}%")

    if args.cpi_resource:
        api_key = get_settings().budget_api_key  # from env or the gitignored .env
        if not api_key:
            print(
                "  ! --cpi-resource given but no BUDGET_API_KEY in env; skipping CPI fetch",
                file=sys.stderr,
            )
        else:
            try:
                cpi = _fetch_latest_cpi(args.cpi_resource, api_key, args.field_cpi)
            except Exception as exc:
                print(f"  ✗ CPI fetch failed ({exc}); pack left unchanged", file=sys.stderr)
                return 1
            if cpi is None:
                print(
                    "  ✗ CPI fetch returned no parseable value; check --field-cpi / resource",
                    file=sys.stderr,
                )
                return 1
            pack["cpi_yoy"] = cpi
            changed.append(f"CPI={cpi}% (fetched)")

    if not changed:
        print(
            "  ! nothing to change — pass --repo-rate/--stance/--cpi or --cpi-resource.",
            file=sys.stderr,
        )
        return 1

    # Derived regime, recomputed for the header (the runtime recomputes it too).
    repo, prev = pack.get("repo_rate"), pack.get("prev_repo_rate")
    pack["regime"] = (
        "easing"
        if (repo is not None and prev and repo < prev)
        else "tightening"
        if (repo is not None and prev and repo > prev)
        else "neutral"
    )
    pack["as_of"] = date.today().strftime("%Y-%m")
    pack["fetched_at"] = date.today().isoformat()

    if args.dry_run:
        print(
            f"  (dry-run) would set: {', '.join(changed)} · regime={pack['regime']}; not written."
        )
        return 0

    pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  ✓ {args.version}: {', '.join(changed)} · regime={pack['regime']} -> {pack_path.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh the bundled RBI rate-cycle pack.")
    ap.add_argument(
        "--version", default="2026", help="Pack version selecting data/rates_<version>.json"
    )
    ap.add_argument("--repo-rate", type=float, default=None, help="latest policy repo rate, %")
    ap.add_argument(
        "--prev-repo-rate",
        type=float,
        default=None,
        help="previous repo rate (default: current pack value)",
    )
    ap.add_argument(
        "--stance", default=None, help="MPC stance: accommodative | neutral | hawkish | …"
    )
    ap.add_argument("--cpi", type=float, default=None, help="set CPI YoY %% directly")
    ap.add_argument(
        "--cpi-resource", default=None, help="data.gov.in OGD resource id for a CPI series"
    )
    ap.add_argument("--field-cpi", default="rate", help="record field holding the CPI YoY value")
    ap.add_argument("--dry-run", action="store_true", help="preview, but do not write the pack")
    args = ap.parse_args()
    print(f"Refreshing rate pack {args.version} into {_DATA_DIR} ...")
    return refresh(args)


if __name__ == "__main__":
    raise SystemExit(main())
