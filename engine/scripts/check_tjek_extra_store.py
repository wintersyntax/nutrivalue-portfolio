#!/usr/bin/env python3
"""TASK-032: check selected PUBLIC Oslo-area Extra shop via one Tjek catalog GET.

--list: read existing owner-private Tjek Oslo store names only, no HTTP.
default: verify configured shop against the private listing, no HTTP.
--live: explicitly perform <=1 store-point /v2/catalogs GET; immutable
private manifest, optional comparison against owner's Oslo-centre catalog.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_extra_store import (  # noqa: E402
    check_public_extra_store, load_public_extra_store,
    validate_public_store,
)
from nutrivalue.providers.tjek_weekly_collect import CollectionError, live_json  # noqa: E402

ARCHIVE = ROOT / "data/private/tjek/weekly"


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--stores-file", required=True, type=Path,
                     help="original saved extra-api-investigation/oslo_stores.json")
    cli.add_argument("--store-name", help="name of one Tjek public Extra shop, "
                     "e.g. 'Pilestredet Park'")
    cli.add_argument("--list", action="store_true", help="list public Extra shop names offline")
    cli.add_argument("--live", action="store_true", help="explicit ONE geographic catalog GET")
    cli.add_argument("--oslo-catalogs-file", type=Path,
                     help="optional original private Oslo-centre catalogs JSON")
    cli.add_argument("--as-of", default=None,
                     help="timezone-aware ISO timestamp, defaults to Oslo now")
    args = cli.parse_args(argv)
    if args.list and (args.store_name or args.live):
        cli.error("--list is standalone")
    if not args.list and not args.store_name:
        cli.error("choose --list or --store-name")
    at = args.as_of or datetime.now(ZoneInfo("Europe/Oslo")).isoformat()
    try:
        if args.list:
            from nutrivalue.providers.tjek_extra_store import load_store_listing
            shops = load_store_listing(stores_path=args.stores_file, private_root=ARCHIVE)
            print("PUBLIC EXTRA SHOPS IN THE SAVED OSLO LISTING (0 HTTP):")
            for shop in sorted(shops, key=lambda x: x["name"].casefold()):
                print(" ", shop["name"])
            return 0
        shop = load_public_extra_store(
            stores_path=args.stores_file, private_root=ARCHIVE,
            store_name=args.store_name,
        )
        if not args.live:
            print("EXTRA SHOP PLAN — ZERO HTTP / ZERO WRITES")
            print("Selected public Extra shop:", shop["name"])
            print("Next action, explicit --live: one scoped /v2/catalogs GET at public shop.")
            print("No source or shelf-price verification from the plan itself.")
            return 0
        report = check_public_extra_store(
            private_root=ARCHIVE, store=shop, as_of=at,
            get_json=lambda endpoint, params: live_json(endpoint, params),
            city_reference_path=args.oslo_catalogs_file,
        )
    except (CollectionError, OSError, ValueError) as exc:
        print(f"EXTRA SHOP: RED — {exc}", file=sys.stderr)
        return 1
    print(f"EXTRA SHOP: {report['status']}; GETs={report['http_requests']}")
    if report["resolution"]:
        result = report["resolution"]
        print("Shop:", result["public_shop_name"])
        print("Current geo catalog:", result["catalog_id"] or "UNRESOLVED")
        print("Same catalog as Oslo-centre reference:",
              result["city_reference_comparison"])
        print("Still source_only — NOT a verified in-store shelf price.")
    if report["halted_reason"]:
        print("HALTED:", report["halted_reason"])
    print("Private manifest: data/private/tjek/weekly/" +
          report["week"] + "/" + report["run_directory"] + "/manifest.json")
    return 0 if report["status"] == "store_point_candidate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
