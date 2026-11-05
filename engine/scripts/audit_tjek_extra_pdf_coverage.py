#!/usr/bin/env python3
"""TASK-032: inspect 103 accessible Extra offers against official Coop Øst PDF.

This is ZERO HTTP and NO OCR. Reads the saved source JSON, geo decision and
official PDF text layer; writes one immutable PRIVATE review-only JSON report.
No guessed 15 missing offers are ever fabricated as Tjek API records.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_extra_pdf_coverage import audit_saved_extra_pdf  # noqa: E402
from nutrivalue.providers.tjek_weekly_collect import CollectionError  # noqa: E402

ARCHIVE = ROOT / "data/private/tjek/weekly"


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--compare-dir", required=True, type=Path,
                     help="existing archived extra-compare-* containing six JSON sources and coop-ost.pdf")
    cli.add_argument("--geo-report", required=True, type=Path,
                     help="saved extra-api-investigation/extra-geo-resolution-v1.json")
    cli.add_argument("--as-of", required=True,
                     help="original capture week; timezone-aware ISO date-time")
    args = cli.parse_args(argv)
    try:
        report = audit_saved_extra_pdf(
            private_root=ARCHIVE, compare_dir=args.compare_dir,
            geo_report_path=args.geo_report, as_of=args.as_of,
        )
    except (OSError, CollectionError) as exc:
        print(f"EXTRA PDF COVERAGE: RED — {exc}", file=sys.stderr)
        return 1
    print("EXTRA PDF COVERAGE: SOURCE_ONLY REVIEW (0 HTTP, no OCR)")
    print("Geo-selected Oslo catalog:", report["catalog_id"])
    print("Provider declared:", report["declared_catalog_offer_count"])
    print("API rows available:", report["available_api_offer_rows"])
    print("Declared/API shortfall:", report["declared_minus_available"])
    print("Observed Tjek offers correlated with PDF text/nearby price:",
          report["matched_tjek_rows_by_heuristic"])
    print("Observed Tjek offers NOT correlated by text heuristic:",
          report["unmatched_tjek_rows_by_heuristic"])
    print("Additional PDF candidate headings for private review:",
          report["pdf_only_possible_heading_candidates"])
    print("VERIFIED extra source-only offers recovered from missing 15:",
          report["verified_new_missing_offers"])
    print("Explanation: PDF-only headings do not establish new Tjek offer IDs "
          "or whether offer_count includes unexposed groups.")
    print("Private review report:",
          args.compare_dir / "extra-pdf-coverage-v1.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
