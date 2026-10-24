#!/usr/bin/env python3
"""TASK-032: automatic Extra catalogue content comparison, with honest region evidence.

Default --plan only inspects existing private metadata. --live performs a
bounded, one-time offer capture of candidate regional Extra catalogues,
then automatically compares exact source facts; NO assumed Coop Øst ID.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_extra_region import (  # noqa: E402
    capture_and_resolve, eligible_catalogs,
)
from nutrivalue.providers.tjek_extra_official import (  # noqa: E402
    match_saved_private,
)
from nutrivalue.providers.tjek_weekly_collect import (  # noqa: E402
    CollectionError, live_json,
)

PRIVATE_ROOT = ROOT / "data/private/tjek/weekly"


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--metadata", type=Path, required=True,
                     help="saved private 80742m.catalogs.json from TASK-032 diagnostic")
    cli.add_argument("--as-of", help="ISO timestamp with zone; defaults to Europe/Oslo now")
    cli.add_argument("--live", action="store_true",
                     help="explicitly permit bounded /v2/offers GETs for Extra")
    args = cli.parse_args(argv)
    at = args.as_of or datetime.now(ZoneInfo("Europe/Oslo")).isoformat()
    if not args.live:
        try:
            # This is a plan, never a source-download.
            records = json.loads(args.metadata.read_text(encoding="utf-8"))
            candidates = eligible_catalogs(records, at)
        except (OSError, ValueError, CollectionError) as exc:
            print(f"EXTRA REGION PLAN: RED — {exc}", file=sys.stderr)
            return 1
        print("EXTRA REGION PLAN — NO HTTP / NO FILES WRITTEN")
        print(f"Region sought: Coop Øst; candidate Extra catalogues: {len(candidates)}")
        print("Maximum 24 serialized Tjek offer GETs and 1 official Coop Øst PDF GET.")
        print("Checks PDF text for ØST and correct ISO week; requires pdftotext.")
        print("If identical: content-equivalent, not region-ID verified.")
        print("If different: shared facts only; Øst-specific catalogue unresolved.")
        print("PDF extractor: " + ("pdftotext available" if shutil.which("pdftotext")
                                   else "MISSING (brew install poppler)"))
        print("Run --live only after synthetic tests succeed.")
        return 0
    if shutil.which("pdftotext") is None:
        print("EXTRA REGION: RED — pdftotext missing; install poppler before --live, "
              "no network used", file=sys.stderr)
        return 1
    try:
        report = capture_and_resolve(
            metadata_path=args.metadata, private_root=PRIVATE_ROOT, as_of=at,
            get_json=lambda ep, params: live_json(ep, params),
        )
    except (CollectionError, OSError) as exc:
        print(f"EXTRA REGION: RED — {exc}", file=sys.stderr)
        return 1
    print(f"EXTRA REGION: {report['status']}; HTTP GETs={report['http_requests']}")
    for cat in report["attempts"]:
        print(f"  {cat['catalog_id']}: {cat['status']}; "
              f"saved={cat.get('available_offers', 0)}; "
              f"shortfall={cat.get('shortfall', 0)}")
    if report["comparison"]:
        res = report["comparison"]
        print(f"Matching facts across ALL candidates: {res['common_offer_facts']}")
        print(f"Source contents identical: {res['same_contents']}")
    if report["halted_reason"]:
        print(f"HALTED: {report['halted_reason']}")
    official = None
    if not report["halted_reason"]:
        folder = PRIVATE_ROOT / report["week"] / report["run_directory"]
        try:
            official = match_saved_private(
                compare_dir=folder, private_root=PRIVATE_ROOT, as_of=at)
            print(f"Official Coop Øst PDF: {official['status']}; "
                  f"candidate={official.get('candidate_for_ost') or 'UNRESOLVED'}")
        except (CollectionError, OSError) as exc:
            print(f"Official Coop Øst PDF: UNRESOLVED — {exc}")
    if official and official.get("candidate_for_ost"):
        print("Evidence: independent PDF product/price correlation, "
              "NOT an authoritative Tjek region ID.")
    else:
        print("Tjek ID for Coop Øst: UNVERIFIED; no guessed regional mapping.")
    print("Private report: data/private/tjek/weekly/" + report["week"] +
          "/" + report["run_directory"] + "/manifest.json")
    return 2 if report["halted_reason"] or not official or (
        official.get("status") != "ost_content_correlated") else 0


if __name__ == "__main__":
    raise SystemExit(main())
