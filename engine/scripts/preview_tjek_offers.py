#!/usr/bin/env python3
"""View PRIVATE locally saved Tjek offers; NEVER fetch or write anything.

Example:
  ./scripts/ci-python scripts/preview_tjek_offers.py \\
    --db data/private/tjek/smoke.sqlite3 \\
    --as-of 2026-10-15T12:00:00+02:00

This is an advertised source-only preview, not a store price check.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_offer_preview import (  # noqa: E402
    TjekPreviewError, format_table, preview_offers,
)


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--db", type=Path, default=ROOT / "data/private/tjek/smoke.sqlite3",
                     help="existing local PRIVATE source-only Tjek staging SQLite")
    cli.add_argument("--as-of", required=True,
                     help="timezone-aware ISO-8601 date/time for source validity")
    cli.add_argument("--include-inactive", action="store_true",
                     help="show expired/future source offers too, each labelled")
    cli.add_argument("--limit", type=int, default=20,
                     help="display at most 1–100 rows (default: 20)")
    args = cli.parse_args(argv)
    try:
        report = preview_offers(args.db, as_of=args.as_of,
                                include_inactive=args.include_inactive,
                                limit=args.limit)
    except TjekPreviewError as exc:
        print(f"TJEK PRIVATE PREVIEW: RED — {exc}", file=sys.stderr)
        return 1
    print(format_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
