#!/usr/bin/env python3
"""TASK-032: rerun Extra/Coop Øst PDF region matching from private files only.

Requires an existing completed Extra regional compare with coop-ost.pdf,
manifest.json and all six Tjek JSON snapshots. No HTTP, no OCR, no SQLite.
Writes a NEW immutable official-ost-match-replay-v2.json in the same folder.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_extra_official import replay_saved_official  # noqa: E402
from nutrivalue.providers.tjek_weekly_collect import CollectionError  # noqa: E402

PRIVATE_ROOT = ROOT / "data/private/tjek/weekly"


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--compare-dir", required=True, type=Path,
                     help="existing data/private/tjek/weekly/YYYY-Www/extra-compare-*/")
    cli.add_argument("--as-of", default=None,
                     help="timezone-aware ISO time; defaults to current Oslo local time")
    args = cli.parse_args(argv)
    at = args.as_of or datetime.now(ZoneInfo("Europe/Oslo")).isoformat()
    try:
        result = replay_saved_official(
            compare_dir=args.compare_dir,
            private_root=PRIVATE_ROOT,
            as_of=at,
        )
    except (OSError, CollectionError) as exc:
        print(f"EXTRA ØST OFFLINE REPLAY: RED — {exc}", file=sys.stderr)
        return 1
    print(f"EXTRA ØST OFFLINE REPLAY: {result['status']}")
    print(f"Official PDF region/week verified: "
          f"{result.get('official_text_region_and_week_verified', False)}")
    print(f"Tjek candidates: {result['source_catalog_count']}; "
          f"declared offer shortfall total: {result['source_declared_offer_shortfall_sum']}")
    scores = result.get("scores", [])
    for score in scores:
        print(f"  {score['catalog_id']}: distinctive={score['unique_source_facts']}; "
              f"official_matches={score['official_pdf_matches']}")
    if result.get("candidate_for_ost"):
        print("Coop Øst content-correlated candidate: " + result["candidate_for_ost"])
        print("This is NOT a verified Tjek store-region mapping.")
    else:
        print("Coop Øst Tjek catalog remains UNRESOLVED: " +
              result.get("unresolved_reason", "not enough unique evidence"))
    print("API requests: 0 Tjek + 0 Coop; original archive and report unchanged.")
    print("New PRIVATE report: " +
          str(args.compare_dir / "official-ost-match-replay-v2.json"))
    return 0 if result["status"] == "ost_content_correlated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
