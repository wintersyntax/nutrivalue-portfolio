#!/usr/bin/env python3
"""Aggregate offline classification of PRIVATE saved Tjek offers.

Recommended: existing archive directory (ignores manifest.json):
  ./scripts/ci-python scripts/audit_offer_categories.py \\
    --archive-dir data/private/tjek/weekly/2026-W42/run-20261016T031849Z \\
    --as-of 2026-10-17T12:00:00+02:00

Alternatives: --input ONE.json TWO.json ... or --db PRIVATE.sqlite3.
Only aggregate counts; never raw offer titles, prices, IDs or paths.
No Tjek API calls, database mutation or file writes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.offer_category_audit import (  # noqa: E402
    OfferCategoryAuditError, audit_saved_offers, audit_saved_archives,
)
from nutrivalue.providers.tjek_offline import CHAINS  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", type=Path,
                        help="existing owner-private Tjek staging DB (read-only)")
    source.add_argument("--input", type=Path, nargs="+",
                        help="1..9 saved single-chain JSON/NDJSON.GZ snapshot files")
    source.add_argument("--archive-dir", type=Path,
                        help="owner-private weekly run folder; read only <dealer-id>.json")
    parser.add_argument("--as-of", required=True,
                        help="timezone-aware local ISO-8601 timestamp")
    args = parser.parse_args(argv)
    try:
        if args.archive_dir is not None:
            if not args.archive_dir.is_dir():
                raise OfferCategoryAuditError("saved archive directory is unavailable")
            paths = [args.archive_dir / f"{dealer}.json"
                     for dealer in CHAINS
                     if (args.archive_dir / f"{dealer}.json").is_file()]
            if not paths:
                raise OfferCategoryAuditError(
                    "no saved selected-chain JSON files in archive directory")
            report = audit_saved_archives(paths, as_of=args.as_of)
        elif args.input is not None:
            report = audit_saved_archives(args.input, as_of=args.as_of)
        else:
            report = audit_saved_offers(args.db, as_of=args.as_of)
    except OfferCategoryAuditError as exc:
        print(f"OFFER CATEGORY AUDIT: NOT GREEN - {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
