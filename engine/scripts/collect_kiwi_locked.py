#!/usr/bin/env python3
"""Replay an existing private KIWI Prislås HTML+JSON snapshot without HTTP.

Example: ./scripts/ci-python scripts/collect_kiwi_locked.py \
  --html data/private/source-probes/kiwi_locked/<sha>.html \
  --report data/private/source-probes/kiwi_locked/<sha>.json \
  --db data/private/kiwi-review.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.kiwi_locked import KiwiLockedError, stage_html  # noqa: E402


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--html", required=True, type=Path)
    cli.add_argument("--report", required=True, type=Path)
    cli.add_argument("--db", type=Path, default=ROOT / "data/private/nutrivalue.sqlite3")
    args = cli.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        count, digest = stage_html(args.html.read_bytes(), report, args.db,
                                   ROOT / "analytics/kiwi_locked_staging.sql")
    except (OSError, ValueError, sqlite3.Error, KiwiLockedError) as exc:
        print(f"KIWI Prislås staging: RED - {exc}", file=sys.stderr)
        return 1
    print(f"KIWI source-only cards staged: {count}; SHA-256: {digest}")
    print("Prices, EAN, nutrition, local store and campaign validity NOT independently verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
