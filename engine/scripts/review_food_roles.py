#!/usr/bin/env python3
"""Seed food-role defaults and PRINT unapproved Matvaretabellen suggestions.

TASK-070 owner approval (one food_key -> one MVT food on the latest snapshot):
  python3 scripts/review_food_roles.py --approve chicken_breast=03.205 \
      --note "owner approved raw fillet as generic yardstick"
  python3 scripts/review_food_roles.py --approved      # list approved references
The form defaults to the food's form_hint; KEY=ID:FORM overrides it.
Approvals are insert-only; nothing else in the database is changed.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.food_roles import (  # noqa: E402
    FoodRoleError, approve_reference, approved_references, load_catalog,
    reference_suggestions, seed_catalog,
)

DEFAULT_DB = ROOT / "data/private/nutrivalue.sqlite3"
SCHEMA = ROOT / "analytics/food_roles_schema.sql"
CATALOG = ROOT / "data/reference/food_roles.json"
APPROVED_QUERY = ROOT / "analytics/queries/food_roles_approved_reference.sql"


def _approval(text: str) -> tuple[str, str, str | None]:
    key, sep, rest = text.partition("=")
    food_id, _, form = rest.partition(":")
    if not sep or not key.strip() or not food_id.strip():
        raise argparse.ArgumentTypeError("use FOOD_KEY=MVT_FOOD_ID[:FORM]")
    return key.strip(), food_id.strip(), form.strip() or None


def show_approved(db) -> None:
    refs = approved_references(db, APPROVED_QUERY)
    print(f"Approved references (latest snapshot): {len(refs)}")
    for key, ref in sorted(refs.items()):
        print(f"{key} -> {ref['mvt_food_id']} {ref['name']} ({ref['form']}): "
              f"protein {ref['protein_g']} g, {ref['kcal']} kcal per 100 g")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--approve", type=_approval, action="append", default=[],
                        metavar="KEY=MVT_ID[:FORM]", help="record an owner approval")
    parser.add_argument("--note", help="review note, required with --approve")
    parser.add_argument("--approved", action="store_true", help="list approved references")
    args = parser.parse_args(argv)
    if args.approve and not (args.note or "").strip():
        parser.error("--approve needs --note")
    if not args.db.is_file():
        print(f"Food role review: RED — existing SQLite file required: {args.db}",
              file=sys.stderr)
        return 1
    try:
        payload = load_catalog(CATALOG)
        with sqlite3.connect(args.db) as db:
            seed_catalog(db, payload, SCHEMA)
            if args.approve or args.approved:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                for key, food_id, form in args.approve:
                    state = approve_reference(db, key, food_id, args.note,
                                              reviewed_at=now, approved_form=form)
                    print(f"{state}: {key} -> {food_id}")
                show_approved(db)
                return 0
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
