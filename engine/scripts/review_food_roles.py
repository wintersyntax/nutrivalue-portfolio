#!/usr/bin/env python3
"""Seed food-role defaults and PRINT unapproved Matvaretabellen suggestions."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.food_roles import (  # noqa: E402
    FoodRoleError, load_catalog, reference_suggestions, seed_catalog,
)

DEFAULT_DB = ROOT / "data/private/nutrivalue.sqlite3"
SCHEMA = ROOT / "analytics/food_roles_schema.sql"
CATALOG = ROOT / "data/reference/food_roles.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print(f"Food role review: RED — existing SQLite file required: {args.db}",
              file=sys.stderr)
        return 1
    try:
        payload = load_catalog(CATALOG)
        with sqlite3.connect(args.db) as db:
            seed_catalog(db, payload, SCHEMA)
            suggestions = reference_suggestions(
                db, payload, limit_per_food=args.limit
            )
            foods_count = db.execute(
                "SELECT COUNT(*) FROM food_role_items"
            ).fetchone()[0]
            substitutions = db.execute(
                "SELECT COUNT(*) FROM food_substitution_rules"
            ).fetchone()[0]
            print(f"Curated roles: {foods_count} foods; "
                  f"{substitutions} context-specific substitutions.")
            if not suggestions:
                print("No reference-food name suggestions found.")
            for key, label, form, snapshot, mvt_id, name, status in suggestions:
                print(f"{key} ({form}) -> {mvt_id} {name} "
                      f"[snapshot {snapshot}; {status}]")
            print("All name suggestions are UNREVIEWED. "
                  "No MVT reference association or price match was approved.")
    except (FoodRoleError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"Food role review: RED — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
