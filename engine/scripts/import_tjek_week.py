#!/usr/bin/env python3
"""TASK-052: OFFLINE import of saved weekly Tjek collector runs into the offer DB.

Examples (no HTTP calls):
  ./scripts/ci-python scripts/import_tjek_week.py --all
  ./scripts/ci-python scripts/import_tjek_week.py --run-dir data/private/tjek/weekly/2026-W43/run-20261019T225318Z
Prints counts only; the database stays PRIVATE under data/private/.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.offer_database import (  # noqa: E402
    OfferImportError, discover_runs, import_run, reclassify,
)

PRIVATE = ROOT / "data" / "private"


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    target = cli.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-dir", type=Path, action="append",
                        help="one saved YYYY-Www/run-* directory (repeatable)")
    target.add_argument("--all", action="store_true",
                        help="every run under --weekly-root, oldest first")
    cli.add_argument("--weekly-root", type=Path, default=PRIVATE / "tjek" / "weekly")
    cli.add_argument("--db", type=Path, default=PRIVATE / "offers.sqlite3",
                     help="PRIVATE SQLite file under data/private/")
    args = cli.parse_args(argv)
    db = args.db.resolve()
    if PRIVATE.resolve() not in db.parents:
        cli.error("--db must be inside data/private/")
    runs = discover_runs(args.weekly_root) if args.all else args.run_dir
    failed = 0
    for run in runs:
        try:
            result = import_run(run, db, ROOT / "analytics")
        except (OfferImportError, OSError, sqlite3.Error) as exc:
            failed += 1
            print(f"RED  {run.parent.name}/{run.name}: {exc}", file=sys.stderr)
            continue
        state = "imported" if result["imported"] else "already present"
        print(f"OK   {result['run_id']}: {state}; {result['offers']} offers")
    if db.is_file():
        added = reclassify(db)
        if added:
            print(f"RECLASSIFIED {added} stored offers with the current taxonomy")
    with sqlite3.connect(db) as conn:
        for week, run_id, offers in conn.execute(
                "SELECT week, run_id, total_saved_offers FROM v_week_run ORDER BY week"):
            print(f"WEEK {week}: selected {run_id} ({offers} offers)")
    print("Source-only chain-level adverts; no checked shelf prices. No HTTP.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
