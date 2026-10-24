#!/usr/bin/env python3
"""Private nine-chain weekly Tjek capture — plan by default; --live makes HTTP.

Examples:
  ./scripts/ci-python scripts/capture_tjek_weekly.py
  ./scripts/ci-python scripts/capture_tjek_weekly.py --live
  ./scripts/ci-python scripts/capture_tjek_weekly.py --live --allow-repeat

Live runs also read catalogue hotspots, recover hotspot offers omitted by the
/offers listing (per-ID GET) and save working page images with a SHA-256
manifest (TASK-051); disable with --no-hotspots / --no-page-images.

Writes only beneath ignored data/private/tjek/weekly/. No DB writes or GCP.
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

from nutrivalue.providers.tjek_extra_store import load_public_extra_store  # noqa: E402

from nutrivalue.providers.tjek_weekly_collect import (  # noqa: E402
    CHAINS, MAX_DETAIL_REQUESTS, MAX_PAGE_IMAGES, MAX_REQUESTS, CollectionError,
    collect_week, live_bytes, live_json,
)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--live", action="store_true",
                     help="explicit authorization to fetch selected Tjek read endpoints")
    cli.add_argument("--allow-repeat", action="store_true",
                     help="explicitly authorize second capture in the same ISO week")
    cli.add_argument("--as-of", default=None,
                     help="ISO datetime with timezone; default current Europe/Oslo time")
    cli.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                     help="hard max 9..100 catalogue/listing/hotspot/page-list GETs; default 80")
    cli.add_argument("--no-hotspots", action="store_true",
                     help="skip hotspot reads and per-ID recovery of omitted offers")
    cli.add_argument("--max-detail-requests", type=int, default=MAX_DETAIL_REQUESTS,
                     help="max per-ID offer detail GETs (0..400); default 200")
    cli.add_argument("--no-page-images", action="store_true",
                     help="skip saving working catalogue page images")
    cli.add_argument("--max-page-images", type=int, default=MAX_PAGE_IMAGES,
                     help="max page image GETs (0..600); default 400")
    cli.add_argument("--extra-stores-file", type=Path,
                     help="saved PRIVATE Tjek extra-api-investigation/oslo_stores.json")
    cli.add_argument("--extra-store-name",
                     help="public Extra shop name in the saved store listing; "
                          "enables one geo-filtered Extra flyer (rather than blank-label exclusion)")
    args = cli.parse_args(argv)
    at = args.as_of or datetime.now(ZoneInfo("Europe/Oslo")).isoformat()
    out = ROOT / "data/private/tjek/weekly"
    if bool(args.extra_stores_file) != bool(args.extra_store_name):
        cli.error("--extra-stores-file and --extra-store-name must be supplied together")
    try:
        extra_store = (
            load_public_extra_store(
                stores_path=args.extra_stores_file,
                private_root=out,
                store_name=args.extra_store_name,
            )
            if args.extra_stores_file is not None else None
        )
    except (OSError, CollectionError) as exc:
        print(f"EXTRA SHOP CONFIG: RED — {exc}", file=sys.stderr)
        return 1
    if not args.live:
        print("TJEK NINE-CHAIN PLAN — NO HTTP / NO FILES WRITTEN")
        print("Scope:", ", ".join(CHAINS.values()))
        print(f"as_of={at}; max_requests={args.max_requests}; serial 0.8s pacing")
        print(f"hotspots={'off' if args.no_hotspots else 'on'} "
              f"(max_detail_requests={args.max_detail_requests}); "
              f"page_images={'off' if args.no_page_images else 'on'} "
              f"(max_page_images={args.max_page_images})")
        print(f"private archive root: {out}")
        if extra_store is not None:
            print(f"Extra shop geofilter: {extra_store['name']} "
                  "(public shop coordinates, one geographically scoped catalog GET).")
        else:
            print("Extra shop geofilter NOT configured: legacy strict-label behavior.")
        print("Use --live for an opt-in bounded fetch. source_only, not checked shelf prices.")
        return 0
    try:
        report = collect_week(
            as_of=at, out_dir=out,
            get_json=lambda endpoint, params: live_json(endpoint, params),
            max_requests=args.max_requests, allow_repeat=args.allow_repeat,
            extra_store=extra_store,
            hotspots=not args.no_hotspots,
            max_detail_requests=args.max_detail_requests,
            page_images=not args.no_page_images, get_bytes=live_bytes,
            max_page_images=args.max_page_images)
    except (OSError, CollectionError) as exc:
        print(f"TJEK CAPTURE: RED — {exc}", file=sys.stderr)
        return 1
    print(f"TJEK NINE-CHAIN CAPTURE: {report['batch_status']}; "
          f"{report['http_requests']} HTTP requests; "
          f"{report['total_saved_offers']} source_only offers; "
          f"{report['offer_detail_requests']} offer detail GETs; "
          f"{report['page_image_requests']} page image GETs")
    for row in report["chains"]:
        print(f"  {row['chain']}: {row['status']}; "
              f"{row['catalogs_selected']} catalogues; "
              f"{row['offers_saved']} saved; shortfall={row['source_count_shortfall']}"
              + (f"; listing={row['listing_offers']} hotspots={row['hotspot_offers']} "
                 f"recovered={row['detail_recovered']}" if "listing_offers" in row else "")
              + (f"; pages={row['page_images_saved']}/{row['pages_listed']}"
                 if "pages_listed" in row else ""))
        if row.get("page_image_error"):
            print("    Page images: " + row["page_image_error"])
        if row.get("validation_error"):
            print("    Validation: " + row["validation_error"] +
                  " (provider data quarantined privately, not imported)")
    if report["halted_reason"]:
        print("HALTED:", report["halted_reason"])
    print("Private files: data/private/tjek/weekly/" + report["week"] +
          "/" + report["run_directory"] + "/")
    print("No Tjek source offers printed. No local-store price verification. No GCP.")
    return 0 if report["batch_status"] == "candidate_complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
