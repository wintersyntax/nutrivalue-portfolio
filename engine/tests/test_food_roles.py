"""Synthetic, offline food-role and reference-matching regression contracts."""
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nutrivalue.food_roles import (
    FoodRoleError, load_catalog, reference_suggestions, seed_catalog,
    validate_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/reference/food_roles.json"
SCHEMA = ROOT / "analytics/food_roles_schema.sql"
QUERY_DIR = ROOT / "analytics/queries"


class FoodRolesTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript(
            (ROOT / "analytics/matvaretabellen_schema.sql").read_text()
        )
        self.payload = load_catalog(CATALOG)
        self.db.executemany(
            "INSERT INTO mvt_snapshots "
            "(id, locale, content_sha256, retrieved_at) VALUES (?,?,?,?)",
            [
                (1, "nb", "synthetic-old", "2026-01-01T00:00:00Z"),
                (2, "nb", "synthetic-new", "2026-10-10T00:00:00Z"),
            ],
        )
        self.db.executemany(
            "INSERT INTO mvt_food_groups "
            "(snapshot_id, group_id, name) VALUES (?,?,?)",
            [(1, "poultry", "Synthetic poultry"),
             (2, "poultry", "Synthetic poultry"),
             (2, "dairy", "Synthetic dairy")],
        )
        self.db.executemany(
            "INSERT INTO mvt_foods "
            "(snapshot_id, food_id, food_name, food_group_id, "
            "energy_kcal, source_uri) VALUES (?,?,?,?,?,?)",
            [
                (1, "COLD", "Kylling, bryst, rå OLD",
                 "poultry", "999", "synthetic:old"),
                (2, "CNEW", "Kylling, bryst, filet, rå",
                 "poultry", "105", "synthetic:new"),
                (2, "COOK", "Kylling, bryst, stekt",
                 "poultry", "165", "synthetic:cooked"),
                (2, "SKYR", "Skyr, naturell",
                 "dairy", "63", "synthetic:skyr"),
            ],
        )
        self.db.executemany(
            "INSERT INTO mvt_nutrients "
            "(snapshot_id, nutrient_id, name, unit) VALUES (?,?,?,?)",
            [(1, "Protein", "Protein", "g"),
             (2, "Protein", "Protein", "g")],
        )
        self.db.executemany(
            "INSERT INTO mvt_food_nutrients "
            "(snapshot_id, food_id, nutrient_id, quantity_per_100g, unit) "
            "VALUES (?,?,'Protein',?,'g')",
            [
                (1, "COLD", "80"),
                (2, "CNEW", "23"),
                (2, "COOK", "31"),
                (2, "SKYR", "11"),
            ],
        )

    def sql(self, name):
        return self.db.execute((QUERY_DIR / name).read_text()).fetchall()

    def test_reference_catalog_has_explicit_roles_and_no_old_prices(self):
        self.assertEqual(len(self.payload["foods"]), 35)
        self.assertEqual(len(self.payload["substitutions"]), 24)
        self.assertEqual(len({x["key"] for x in self.payload["foods"]}), 35)
        for item in self.payload["foods"]:
            self.assertNotIn("price", item)
            self.assertNotIn("protein_per_100g", item)
            self.assertNotIn("ean", item)
        item = next(x for x in self.payload["foods"] if x["key"] == "mager_kesam")
        self.assertEqual(item["tier"], "core")
        self.assertIn("breakfast_protein", item["roles"])

    def test_seed_idempotent_with_contextual_substitutions(self):
        seed_catalog(self.db, self.payload, SCHEMA)
        first = self.db.total_changes
        seed_catalog(self.db, self.payload, SCHEMA)
        self.assertEqual(self.db.total_changes, first)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM food_role_items").fetchone()[0],
            35,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM food_substitution_rules"
            ).fetchone()[0],
            24,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT caution FROM food_substitution_rules "
                "WHERE recipe_context='mince_sauce' "
                "AND from_food_key='beef_mince' "
                "AND to_food_key='red_lentils'"
            ).fetchone()[0],
            "Partial substitution only: lentils add carbohydrate and fiber.",
        )
        report = self.sql("food_roles_overview.sql")
        self.assertEqual(len(report), 35)
        self.assertIn("quick_protein", next(
            r[4] for r in report if r[0] == "tuna_drained"
        ))

    def test_substitution_is_directional_and_partial_lentils_are_flagged(self):
        seed_catalog(self.db, self.payload, SCHEMA)
        rows = self.sql("food_roles_substitutions.sql")
        partial = [r for r in rows if r[0] == "mince_sauce"
                   and r[1] == "beef_mince"
                   and r[3] == "red_lentils"]
        self.assertEqual(len(partial), 1)
        self.assertEqual(partial[0][5], "partial")
        reverse = [r for r in rows if r[1] == "red_lentils"
                   and r[3] == "beef_mince"]
        self.assertEqual(reverse, [])

    def test_invalid_catalog_rejected_before_any_table_created(self):
        mutants = []
        a = copy.deepcopy(self.payload)
        a["foods"][1]["key"] = a["foods"][0]["key"]
        mutants.append(a)
        b = copy.deepcopy(self.payload)
        b["foods"][0]["roles"].append("fictional_role")
        mutants.append(b)
        c = copy.deepcopy(self.payload)
        c["substitutions"][0]["to"] = "not_food"
        mutants.append(c)
        d = copy.deepcopy(self.payload)
        d["substitutions"][0]["note"] = ""
        mutants.append(d)
        for invalid in mutants:
            with self.subTest(invalid=invalid["foods"][0]["key"]):
                with self.assertRaises(FoodRoleError):
                    seed_catalog(self.db, invalid, SCHEMA)
                self.assertIsNone(self.db.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name='food_role_items'"
                ).fetchone())

    def test_changed_seed_does_not_override_existing_catalog(self):
        seed_catalog(self.db, self.payload, SCHEMA)
        changed = copy.deepcopy(self.payload)
        changed["foods"][0]["label"] = "Changed label"
        with self.assertRaises(FoodRoleError):
            seed_catalog(self.db, changed, SCHEMA)
        value = self.db.execute(
            "SELECT food_label FROM food_role_items "
            "WHERE food_key='chicken_breast'"
        ).fetchone()[0]
        self.assertEqual(value, "Kyllingfilet")

    def test_candidate_search_is_read_only_unreviewed_and_latest_only(self):
        seed_catalog(self.db, self.payload, SCHEMA)
        before = self.db.total_changes
        suggestions = reference_suggestions(self.db, self.payload)
        self.assertEqual(self.db.total_changes, before)
        breast = [s for s in suggestions if s[0] == "chicken_breast"]
        self.assertTrue(breast)
        self.assertEqual({s[3] for s in breast}, {2})
        self.assertNotIn("COLD", {s[4] for s in breast})
        self.assertEqual({s[6] for s in suggestions}, {"UNREVIEWED"})
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM food_reference_reviews"
        ).fetchone()[0], 0)
        self.assertEqual(self.sql("food_roles_approved_reference.sql"), [])

    def test_only_manually_reviewed_latest_reference_match_is_visible(self):
        seed_catalog(self.db, self.payload, SCHEMA)
        self.db.execute(
            "INSERT INTO food_reference_reviews "
            "(food_key, snapshot_id, mvt_food_id, reviewed_at, "
            "reviewer_note, approved_form) "
            "VALUES ('chicken_breast', 1, 'COLD', "
            "'2026-01-01T00:00:00Z', 'Synthetic old snapshot', 'raw')"
        )
        self.assertEqual(self.sql("food_roles_approved_reference.sql"), [])
        self.db.execute(
            "INSERT INTO food_reference_reviews "
            "(food_key, snapshot_id, mvt_food_id, reviewed_at, "
            "reviewer_note, approved_form) "
            "VALUES ('chicken_breast', 2, 'CNEW', "
            "'2026-10-10T00:00:00Z', 'Synthetic human-reviewed food form', 'raw')"
        )
        rows = self.sql("food_roles_approved_reference.sql")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0:3], ("chicken_breast", "Kyllingfilet", "core"))
        self.assertEqual(rows[0][4:8],
                         ("CNEW", "Kylling, bryst, filet, rå", 23.0, 105.0))
        self.assertEqual(rows[0][8],
                         "2026-10-10T00:00:00Z")

    def test_foreign_keys_block_nonexistent_reference_and_empty_review(self):
        seed_catalog(self.db, self.payload, SCHEMA)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO food_reference_reviews "
                "(food_key, snapshot_id, mvt_food_id, reviewed_at, "
                "reviewer_note, approved_form) "
                "VALUES ('chicken_breast', 2, 'FABRICATED', "
                "'2026-10-10T00:00:00Z', 'fake', 'raw')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO food_reference_reviews "
                "(food_key, snapshot_id, mvt_food_id, reviewed_at, "
                "reviewer_note, approved_form) "
                "VALUES ('chicken_breast', 2, 'CNEW', "
                "'2026-10-10T00:00:00Z', '', 'raw')"
            )

    def test_db_load_existing_reference_file_reproducibly(self):
        # Does not require live APIs or actual shopping prices.
        with tempfile.TemporaryDirectory() as temp:
            dbfile = Path(temp) / "private.sqlite3"
            with sqlite3.connect(dbfile) as conn:
                conn.executescript(
                    (ROOT / "analytics/matvaretabellen_schema.sql").read_text()
                )
                seed_catalog(conn, self.payload, SCHEMA)
                result = conn.execute(
                    "SELECT COUNT(*) FROM food_role_items"
                ).fetchone()[0]
            self.assertEqual(result, 35)


if __name__ == "__main__":
    unittest.main()
