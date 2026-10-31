"""TASK-080: per-serving nutrition of Mealie recipes (synthetic data only)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nutrivalue.mealie_read import import_snapshot, load_snapshot, save_snapshot
from nutrivalue.recipe_nutrition import (
    RecipeNutritionError, add_label, map_ingredient, recipe_nutrition, report, search_mvt,
    suggestions,
)
from test_mealie_read import FakeMealie, ing, reader, unit

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "analytics"
NOW = "2026-10-30T21:00:00+00:00"

RECIPE = {"synthetic-porridge": {
    "id": "33333333-3333-3333-3333-333333333333", "slug": "synthetic-porridge",
    "name": "Synthetic porridge", "recipeServings": 2,
    "recipeIngredient": [ing(100, unit("gram", "g"), "steel cut oat"),
                         ing(0.5, unit("liter"), "almond milk"),
                         ing(40, unit("gram", "g"), "protein powder"),
                         ing(1, None, "banana")]}}

# Synthetic reference values (round numbers, not real Matvaretabellen data).
MVT = {"T.001": ("Synthetic oats", 400, {"Protein": 10, "Fett": 5, "Karbo": 60, "Fiber": 10}),
       "T.002": ("Synthetic almond drink", 20, {"Protein": 1, "Fett": 1, "Karbo": 1}),
       "T.003": ("Synthetic banana", 100, {"Protein": 1, "Fett": 0, "Karbo": 20, "Fiber": 2})}


def database(tmp: Path, recipes=RECIPE) -> sqlite3.Connection:
    db = sqlite3.connect(tmp / "nv.sqlite3")
    for name in ("matvaretabellen_schema.sql", "food_roles_schema.sql",
                 "recipe_read_model.sql", "recipe_nutrition.sql"):
        db.executescript((A / name).read_text())
    db.execute("INSERT INTO mvt_snapshots VALUES (1,'nb','x','2026-10-01T00:00:00Z','test')")
    db.execute("INSERT INTO mvt_food_groups VALUES (1,'g','Group',NULL)")
    for nid in ("Protein", "Fett", "Karbo", "Fiber"):
        db.execute("INSERT INTO mvt_nutrients VALUES (1,?,?, 'g', NULL, 1)", (nid, nid))
    for fid, (name, kcal, nutrients) in MVT.items():
        db.execute("INSERT INTO mvt_foods VALUES (1,?,?,'g',NULL,'100',?,NULL)", (fid, name, str(kcal)))
        for nid, value in nutrients.items():
            db.execute("INSERT INTO mvt_food_nutrients VALUES (1,?,?,?,'g',NULL)",
                       (fid, nid, str(value)))
    folder = save_snapshot(reader(FakeMealie(recipes)), tmp / "snap",
                           now=datetime(2026, 10, 30, 20, tzinfo=timezone.utc))
    import_snapshot(db, load_snapshot(folder), A / "recipe_read_model.sql")
    db.commit()
    return db


class RecipeNutritionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = database(Path(tmp.name))
        self.addCleanup(self.db.close)

    def test_unmapped_recipe_is_incomplete_and_lists_gaps(self):
        [r] = recipe_nutrition(self.db)
        self.assertEqual(r["status"], "incomplete")
        self.assertEqual(r["per_serving"]["kcal"], 0)
        self.assertEqual(len(r["gaps"]), 4)
        self.assertIn("INCOMPLETE", "\n".join(report([r])))

    def test_mvt_label_and_density_give_per_serving_values(self):
        map_ingredient(self.db, "Steel cut oat", "mvt:T.001", note="oats", reviewed_at=NOW)
        # almond milk in litres: grams only with the owner's density
        map_ingredient(self.db, "almond milk", "mvt:T.002", note="drink", reviewed_at=NOW)
        [r] = recipe_nutrition(self.db)
        milk = next(l for l in r["lines"] if l["ingredient"] == "almond milk")
        self.assertEqual(milk["evidence"], "no_density")
        map_ingredient(self.db, "almond milk", "mvt:T.002", note="drink, label says ~1.0",
                       reviewed_at="2026-10-30T21:01:00+00:00", density_g_per_ml=1.0)
        add_label(self.db, "Synthetic protein", basis="per_100ml", kcal=200, protein_g=40,
                  fat_g=2, carbs_g=4, density_g_per_ml=0.5, note="synthetic label",
                  entered_at=NOW)            # per 100 ml with 0.5 g/ml -> 400 kcal per 100 g
        map_ingredient(self.db, "protein powder", "label:synthetic protein", note="label",
                       reviewed_at=NOW)
        map_ingredient(self.db, "banana", "mvt:T.003", note="banana", reviewed_at=NOW)
        [r] = recipe_nutrition(self.db)
        # banana is "1 piece": no grams, so still incomplete
        self.assertEqual(r["status"], "incomplete")
        self.assertIn("banana: no_grams", r["gaps"])
        # 100 g oats 400 + 500 g drink 100 + 40 g protein 160 = 660 kcal / 2 servings
        self.assertEqual(r["per_serving"]["kcal"], 330.0)
        # protein 10 + 5 + (40 per 100 ml / 0.5 g/ml = 80 per 100 g) * 40 g = 32 -> 47 / 2
        self.assertEqual(r["per_serving"]["protein_g"], 23.5)
        self.assertEqual(r["per_serving"]["fiber_g"], None)     # drink/label lack fiber
        self.assertEqual(sorted(r["evidence"]),
                         ["generic_estimate", "no_grams", "owner_label"])

    def test_complete_recipe_and_inputs_are_insert_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe = {k: dict(v) for k, v in RECIPE.items()}
            only = recipe["synthetic-porridge"]
            only["recipeIngredient"] = only["recipeIngredient"][:1]     # 100 g oats
            db = database(Path(tmp), recipe)
            map_ingredient(db, "steel cut oat", "mvt:T.001", note="n", reviewed_at=NOW)
            [r] = recipe_nutrition(db)
            self.assertEqual((r["status"], r["gaps"]), ("complete_estimate", []))
            self.assertEqual(r["per_serving"], {"kcal": 200.0, "protein_g": 5.0, "fat_g": 2.5,
                                                "carbs_g": 30.0, "fiber_g": 5.0})
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE recipe_ingredient_sources SET note='x'")
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM recipe_ingredient_sources")
            db.close()

    def test_bad_inputs_are_refused(self):
        with self.assertRaises(RecipeNutritionError):
            map_ingredient(self.db, "oats", "mvt:NOPE", note="n", reviewed_at=NOW)
        with self.assertRaises(RecipeNutritionError):
            map_ingredient(self.db, "oats", "label:missing", note="n", reviewed_at=NOW)
        with self.assertRaises(RecipeNutritionError):
            map_ingredient(self.db, "oats", "web:x", note="n", reviewed_at=NOW)
        with self.assertRaises(RecipeNutritionError):
            map_ingredient(self.db, "oats", "mvt:T.001", note=" ", reviewed_at=NOW)
        with self.assertRaises(RecipeNutritionError):
            add_label(self.db, "x", basis="per_100ml", kcal=1, protein_g=1, note="n",
                      entered_at=NOW)           # per 100 ml needs a density
        with self.assertRaises(RecipeNutritionError):
            map_ingredient(self.db, "almond milk", "mvt:T.002", note="n", reviewed_at=NOW,
                           density_g_per_ml=9)

    def test_suggestions_use_approved_food_keys_and_search_finds_ids(self):
        self.db.execute("INSERT INTO food_role_items VALUES ('oats','Havregryn','core','dry')")
        self.db.execute("INSERT INTO food_reference_reviews (food_key, snapshot_id, mvt_food_id, "
                        "reviewed_at, reviewer_note, approved_form) VALUES "
                        "('oats',1,'T.001',?, 'synthetic','dry')", (NOW,))
        hits = suggestions(self.db, A / "queries" / "food_roles_approved_reference.sql")
        self.assertTrue(hits["steel cut oat"][0].startswith("mvt:T.001"))
        self.assertEqual(hits["banana"], [])
        self.assertEqual(search_mvt(self.db, "almond")[0][0], "T.002")


if __name__ == "__main__":
    unittest.main()
