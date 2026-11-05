#!/usr/bin/env python3
"""Read ONLY already-staged private KIWI/REMA observations, with partial coverage.

A retailer not yet staged is reported as unavailable, never as zero exceptions.
One retailer with incomplete or changed staging schema is an error, not skipped.
No network, schema creation, data writing or automatic product matching.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
QUERY = ROOT / 'analytics/queries/reconciliation_exception_queue.sql'

# Fixed, trusted SQL identifiers. For an entirely absent retailer, a zero-row
# in-memory CTE can satisfy the UNION's shape without mutating the private DB.
RETAILER_TABLES = {
    'KIWI': {
        'kiwi_locked_snapshots': ('sha256', 'fetched_at', 'source_url'),
        'kiwi_locked_cards': (
            'snapshot_sha256', 'card_no', 'product_name', 'details_text',
            'price_ore', 'price_basis', 'deposit_applies', 'package_quantity',
            'package_unit', 'pack_count', 'unit_price_ore', 'unit_price_unit',
            'unit_price_check', 'review_state',
        ),
    },
    'REMA': {
        'rema_locked_snapshots': ('sha256', 'fetched_at', 'source_url'),
        'rema_locked_cards': (
            'snapshot_sha256', 'card_no', 'product_name', 'details_text',
            'price_ore', 'price_basis', 'package_quantity', 'package_unit',
            'pack_count', 'unit_price_ore', 'unit_price_unit', 'review_state',
        ),
    },
}


def _source_query(db: sqlite3.Connection) -> tuple[str, list[str], list[str]]:
    """Build a SQL SELECT from actual staged sources; never guess missing data."""
    existing = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    available = []
    skipped = []
    empty_ctes = []
    for retailer, definitions in RETAILER_TABLES.items():
        present = set(definitions) & existing
        if present and len(present) != len(definitions):
            missing = sorted(set(definitions) - present)
            raise ValueError(
                f'{retailer} staging is incomplete: missing {", ".join(missing)}'
            )
        if not present:
            skipped.append(retailer)
            for table, cols in definitions.items():
                empty_ctes.append(
                    f"{table} ({', '.join(cols)}) AS "
                    f"(SELECT {', '.join('NULL' for _ in cols)} WHERE 0)"
                )
            continue
        for table, required in definitions.items():
            # Table names are constants above, never user-supplied SQL.
            columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
            missing = set(required) - columns
            if missing:
                raise ValueError(
                    f'{retailer} staging schema changed: {table} missing '
                    f'{", ".join(sorted(missing))}'
                )
        available.append(retailer)

    if not available:
        raise ValueError(
            'No KIWI or REMA locked-price staging tables in this private DB. '
            'Stage a saved retailer snapshot using the existing collector, '
            'or choose the private DB containing those tables; no results inferred.'
        )

    query = QUERY.read_text(encoding='utf-8')
    if empty_ctes:
        marker = 'WITH kiwi_latest AS ('
        if query.count(marker) != 1:
            raise ValueError('reconciliation SQL query template changed')
        # Entirely absent retailer = zero rows at query time. Nothing is
        # created in SQLite and existing retailer source rows are untouched.
        query = query.replace(
            marker, 'WITH ' + ',\n'.join(empty_ctes) + ',\nkiwi_latest AS (', 1
        )
    return query, available, skipped


def inspect(db_path: Path, limit: int = 5) -> dict:
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError('limit must be 1..5')
    if not db_path.is_file():
        raise ValueError('private staged SQLite file not found')
    # URI mode=ro plus query_only ensure source facts are never changed.
    with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA busy_timeout=3000')
        query, available, skipped = _source_query(db)
        rows = db.execute(query).fetchall()

    out = [dict(row) for row in rows[:limit]]
    return {
        'kind': 'private_reconciliation_exception_preview',
        'available_retailers': available,
        'skipped_retailers_without_staging': skipped,
        'coverage': 'partial' if skipped else 'both_retailers_staged',
        'source_rows_with_exceptions': len(rows),
        'selected_count': len(out),
        'requested_limit': limit,
        'selection': 'deterministic possible-food name hint ONLY; manual selection required',
        'identity_status': 'not_confirmed',
        'nutrition_status': 'not_confirmed',
        'retail_price_status': 'source_only',
        'lookup_attempts': 'not_attempted',
        'cards': out,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path, default=ROOT / 'data/private/nutrivalue.sqlite3')
    ap.add_argument('--limit', type=int, default=5)
    args = ap.parse_args(argv)
    try:
        report = inspect(args.db, args.limit)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print('RECONCILIATION QUEUE: RED - ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
