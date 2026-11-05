#!/usr/bin/env python3
"""TASK-023: OFFLINE stage a previously saved PRIVATE Tjek JSON/NDJSON archive.

Examples (no HTTP calls):
  ./scripts/ci-python scripts/collect_tjek_offline.py --input data/private/tjek/week.json --dry-run
  ./scripts/ci-python scripts/collect_tjek_offline.py --input data/private/tjek/week.ndjson.gz
Input formats are documented in docs/data-sources.md. NEVER commit actual
Tjek offer files, home/store coordinates, or user permission correspondence.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.tjek_offline import (  # noqa: E402
    TjekSnapshotError, parse_snapshot, stage_snapshot,
)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", required=True, type=Path,
                     help="Existing local .json or kundeavis .ndjson.gz snapshot; never fetched")
    cli.add_argument("--db", type=Path, default=ROOT / "data/private/nutrivalue.sqlite3",
                     help="PRIVATE SQLite database, never a Git-tracked or public file")
    cli.add_argument("--dry-run", action="store_true", help="Parse and audit, never write SQLite")
    args = cli.parse_args(argv)
    try:
        snapshot = parse_snapshot(args.input.read_bytes(), args.input.name)
        if args.dry_run:
            print("TJEK OFFLINE: DRY-RUN — parsed, no DB writes.")
            stored = False
            count = len(snapshot.offers)
        else:
            count, stored = stage_snapshot(
                snapshot, args.db, ROOT / "analytics/tjek_offer_staging.sql"
            )
    except (OSError, ValueError, sqlite3.Error, TjekSnapshotError) as exc:
        print(f"TJEK OFFLINE: RED — {exc}", file=sys.stderr)
        return 1
    chains = sorted({row[2] for row in snapshot.catalogs})
    print(f"TJEK OFFLINE: {count} source-only offers, {len(snapshot.catalogs)} catalogs, "
          f"{len(chains)} selected chains; {'new snapshot' if stored else 'no mutation/dry-run or replay'}.")
    print(f"Snapshot SHA-256: {snapshot.sha256}; original fetched_at: {snapshot.fetched_at}")
    print("No API requests. No matched EAN, local-store price, nutrition or verified ranking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
