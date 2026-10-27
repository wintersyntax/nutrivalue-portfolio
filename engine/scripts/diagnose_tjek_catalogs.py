#!/usr/bin/env python3
"""Inspect catalog metadata for four missing Tjek chains; OFFLINE plan default.

--live makes at most four catalog-list GETs, no offer requests.
Writes PRIVATE catalog metadata + summary beneath weekly/diagnostic-*/.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_catalog_diagnostic import (  # noqa: E402
    MISSING_DEALERS, diagnose_missing_catalogs,
)
from nutrivalue.providers.tjek_offline import CHAINS  # noqa: E402
from nutrivalue.providers.tjek_weekly_collect import (  # noqa: E402
    CollectionError, _aware, live_json,
)

PRIVATE_WEEKLY = ROOT / "data/private/tjek/weekly"


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--live", action="store_true",
                     help="explicitly allow four scoped Tjek catalog GETs")
    cli.add_argument("--as-of", help="timezone-aware ISO timestamp; defaults to now in Oslo")
    args = cli.parse_args(argv)
    at = args.as_of or datetime.now(ZoneInfo("Europe/Oslo")).isoformat()
    try:
        parsed = _aware(at)
    except CollectionError as exc:
        print(f"TJEK DIAGNOSTIC: RED — {exc}", file=sys.stderr)
        return 1
    if not args.live:
        print("TJEK CATALOG DIAGNOSTIC PLAN — NO HTTP / NO FILES")
        print("Targets: " + ", ".join(CHAINS[d] for d in MISSING_DEALERS))
        print("Maximum four GET /v2/catalogs (one/dealer), zero offer GETs.")
        print(f"As of: {parsed.isoformat()}; private archive: {PRIVATE_WEEKLY}")
        print("Run with --live for a single private, metadata-only diagnostic.")
        return 0
    try:
        report = diagnose_missing_catalogs(
            as_of=parsed.isoformat(), out_dir=PRIVATE_WEEKLY,
            get_json=lambda endpoint, params: live_json(endpoint, params),
        )
    except (OSError, CollectionError) as exc:
        print(f"TJEK DIAGNOSTIC: RED — {exc}", file=sys.stderr)
        return 1
    print(f"TJEK CATALOG DIAGNOSTIC: {report['http_requests']} GETs; "
          "0 offer GETs; private metadata only")
    for row in report["chains"]:
        print(f"  {row['chain']}: {row['status']}")
        if row["status"] == "diagnosed":
            print(f"    returned={row['returned_catalogs']}, "
                  f"eligible_weekly={row['weekly_filter_matches']}, "
                  f"limit_100={row['response_limit_reached']}")
            print("    reasons: " + ", ".join(
                f"{reason}={count}" for reason, count in row["reasons"].items()
                if count
            ))
    if report["halted_reason"]:
        print("HALTED: " + report["halted_reason"])
    print("Private files: data/private/tjek/weekly/" +
          report["week"] + "/" + report["run_directory"] + "/")
    print("Do not publish raw catalog metadata; no prices, no GCP.")
    return 0 if not report["halted_reason"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
