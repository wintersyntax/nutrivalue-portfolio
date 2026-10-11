#!/usr/bin/env python3
"""Execute and print the public SQL reports with Python's standard sqlite3."""
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
DB = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'data'/'demo.sqlite3'
QUERIES = [
  'demo_protein_value.sql', 'best_protein_value.sql',
  'mvt_nutrition_coverage.sql', 'mvt_protein_per_100kcal.sql',
  'mvt_portions_for_25g_protein.sql', 'mvt_reference_group_summary.sql'
]
with sqlite3.connect(DB) as db:
    db.row_factory=sqlite3.Row
    for filename in QUERIES:
        sql=(ROOT/'analytics'/'queries'/filename).read_text()
        rows=db.execute(sql).fetchall()
        print(f'\n### {filename} | {len(rows)} row(s)')
        if rows:
            print(' | '.join(rows[0].keys()))
            for row in rows:
                print(' | '.join(str(x) if x is not None else 'NULL' for x in row))
