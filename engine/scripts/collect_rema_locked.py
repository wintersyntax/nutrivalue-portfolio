#!/usr/bin/env python3
"""Stage an already saved REMA Låst pris HTML snapshot, without network use.

Example: ./scripts/ci-python scripts/collect_rema_locked.py --html /private/abc.html
  --report /private/abc.json --db data/private/nutrivalue.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from nutrivalue.providers.rema_locked import RemaLockedError, stage_html  # noqa: E402


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--html', required=True, type=Path, help='Existing private REMA snapshot')
    cli.add_argument('--report', required=True, type=Path, help='Its existing hashed JSON probe report')
    cli.add_argument('--db', type=Path, default=ROOT / 'data/private/nutrivalue.sqlite3')
    args = cli.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding='utf-8'))
        total, digest = stage_html(args.html.read_bytes(), report, args.db,
                                   ROOT / 'analytics/rema_locked_staging.sql')
    except (OSError, ValueError, sqlite3.Error, RemaLockedError) as exc:
        print(f'REMA locked-price staging: RED - {exc}', file=sys.stderr)
        return 1
    print(f'REMA source-only cards staged: {total}; snapshot SHA-256: {digest}')
    print('Price/EAN/nutrition/store/offer validity NOT independently verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
