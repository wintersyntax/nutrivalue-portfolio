#!/usr/bin/env python3
"""Offline recipe nutrition estimates from a locally supplied recipe read model.

Use --suggest or --search to review generic reference foods; --map records
an explicit source review and --label records user-reported package values.
Missing mass, density, servings or nutrition remains unresolved. No network.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.recipe_nutrition import (  # noqa: E402
    RecipeNutritionError, add_label, map_ingredient, recipe_nutrition, report, search_mvt,
    suggestions,
)

PRIVATE = ROOT / "data" / "private"
SCHEMAS = [ROOT / "analytics" / n for n in
           ("recipe_read_model.sql", "recipe_nutrition.sql")]
APPROVED = ROOT / "analytics" / "queries" / "food_roles_approved_reference.sql"


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--db", type=Path, default=PRIVATE / "nutrivalue.sqlite3")
    cli.add_argument("--suggest", action="store_true")
    cli.add_argument("--search")
    cli.add_argument("--map", help='"<Mealie food name>=mvt:<id>" or "=label:<label name>"')
    cli.add_argument("--density", type=float, help="g per ml for an ingredient given in ml")
    cli.add_argument("--label", help="name of a package label to store")
    cli.add_argument("--basis", choices=("per_100g", "per_100ml"), default="per_100g")
    cli.add_argument("--label-density", type=float, help="g per ml (needed for per_100ml)")
    for name in ("kcal", "protein", "fat", "carbs", "fiber"):
        cli.add_argument(f"--{name}", type=float)
    cli.add_argument("--note", default="")
    args = cli.parse_args(argv)
    db_path = args.db.resolve()
    if PRIVATE.resolve() not in db_path.parents or not db_path.is_file():
        cli.error("--db must be an existing database inside data/private/")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(db_path) as db:
            for schema in SCHEMAS:
                db.executescript(schema.read_text(encoding="utf-8"))
            if args.label:
                if args.kcal is None or args.protein is None:
                    cli.error("--label needs at least --kcal and --protein")
                add_label(db, args.label, basis=args.basis, kcal=args.kcal,
                          protein_g=args.protein, fat_g=args.fat, carbs_g=args.carbs,
                          fiber_g=args.fiber, density_g_per_ml=args.label_density,
                          note=args.note, entered_at=now)
                print(f"Stored label {args.label!r} ({args.basis}).")
            if args.map:
                ingredient, sep, source = args.map.partition("=")
                if not sep:
                    cli.error('--map needs "<ingredient>=<source>"')
                print("Mapped " + map_ingredient(db, ingredient, source, note=args.note,
                                                 reviewed_at=now,
                                                 density_g_per_ml=args.density))
            if args.search:
                for row in search_mvt(db, args.search):
                    print(f"{row[0]}  {row[1]}  ({row[2] or '?'} kcal/100 g)")
                return 0
            if args.suggest:
                for food, hits in suggestions(db, APPROVED).items():
                    print(f"- {food}: " + ("; ".join(hits) if hits else
                                            "no approved match; use --search <word>"))
                return 0
            if not (args.label or args.map):
                print("\n".join(report(recipe_nutrition(db))))
    except (RecipeNutritionError, sqlite3.Error) as exc:
        print(f"RED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
