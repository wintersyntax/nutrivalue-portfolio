#!/usr/bin/env python3
"""Audit saved PRIVATE Tjek snapshots for nine-chain weekly coverage (NO HTTP).

Use one --input per saved chain; requires explicit timezone-aware --as-of.
Exit 0: locally candidate-complete nine-chain inputs (NOT price verification).
Exit 2: missing/stale/partial/unproven coverage; exit 1: unsafe inputs.
No SQLite or cloud resources are read or written and no source offers print.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_weekly_audit import (  # noqa: E402
    WeeklyAuditError, audit_saved_week,
)


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, action="append", required=True,
                     help="existing private .json or .ndjson.gz; repeat per selected chain")
    cli.add_argument("--as-of", required=True,
                     help="timezone-aware ISO-8601 local time for validity checks")
    args = cli.parse_args(argv)
    try:
        report = audit_saved_week(args.input, as_of=args.as_of)
    except WeeklyAuditError as exc:
        print(f"TJEK WEEKLY AUDIT: RED — {exc}", file=sys.stderr)
        return 1
    state = ("CANDIDATE ONLY" if report["batch_status"] == "candidate_complete"
             else "PARTIAL / UNPROVEN")
    print(f"TJEK WEEKLY AUDIT: {state} — NO HTTP / NO DATABASE WRITES")
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["batch_status"] == "candidate_complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
