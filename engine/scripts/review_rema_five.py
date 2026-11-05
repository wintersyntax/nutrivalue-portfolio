#!/usr/bin/env python3
"""TASK-021: inspect five pinned real REMA cards vs existing private Kassalapp staging.

Default preview is read-only. --apply writes an immutable review-only report
to a DIFFERENT ignored/private SQLite DB; source input DBs remain untouched.
No HTTP, store price verification, nutrition promotions or EAN inference.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from nutrivalue.reconciliation_pilot import inspect,store  # noqa: E402


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--rema-db',type=Path,default=ROOT/'data/private/rema-review.sqlite3')
    ap.add_argument('--kassalapp-db',type=Path,default=ROOT/'data/private/nutrivalue.sqlite3')
    ap.add_argument('--snapshot-sha',required=True)
    ap.add_argument('--cards',required=True,help='exactly five comma-separated card IDs')
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--output-db',type=Path,default=ROOT/'data/private/reconciliation-pilot.sqlite3')
    args=ap.parse_args(argv)
    try:
        numbers=tuple(int(x) for x in args.cards.split(','))
        report=inspect(args.rema_db,args.kassalapp_db,args.snapshot_sha,numbers)
        if args.apply:
            if args.output_db.resolve() in (args.rema_db.resolve(),args.kassalapp_db.resolve()):
                raise ValueError('never write a source SQLite database')
            added,replayed=store(report,args.output_db,
                ROOT/'analytics/reconciliation_pilot_schema.sql',ROOT/'data/private')
            print('PRIVATE PILOT: '+str(added)+' new card reviews, '
                  +str(replayed)+' identical replays (source_only).')
            print('PRIVATE REPORT DB: '+str(args.output_db))
        else:
            # User-facing previews are value-redacted. Actual names, published
            # prices and provider IDs stay in the owner-private source DBs and
            # the optional separate ignored review DB, not chat/paste output.
            safe = {
                'kind': report['kind'],
                'status': report['status'],
                'selected_count': len(report['cases']),
                'local_kassalapp_rows_scanned': report['kassalapp_staging_rows_seen'],
                'manual_external_lookups': report['manual_external_lookups'],
                'cases': [{
                    'card_no': case['card_no'],
                    'result': case['result'],
                    'reasons': case['reasons'],
                    'local_name_candidate_count': case['lookup']['matching_rows'],
                    'local_lookup_result': case['lookup']['result'],
                    'identity_confirmed': case['identity_confirmed'],
                    'nutrition_verified': case['nutrition_verified'],
                    'local_retail_price_verified': case['local_retail_price_verified'],
                } for case in report['cases']],
            }
            print(json.dumps(safe,ensure_ascii=False,indent=2))
            print('READ-ONLY PREVIEW: source values redacted; no source or review DB modified.')
    except (ValueError,sqlite3.Error,OSError) as exc:
        print('REMA FIVE-CARD PILOT: RED - '+str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
