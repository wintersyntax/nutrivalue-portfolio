"""SQL portfolio contracts: all rows here are synthetic, never retailer data."""
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUERIES = ROOT / "analytics/queries"


class NutritionPortfolioSqlTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript((ROOT / "analytics/matvaretabellen_schema.sql").read_text())
        self.db.executescript((ROOT / "analytics/kiwi_pricecheck_staging.sql").read_text())
        # An older snapshot must NEVER bleed into latest-snapshot results.
        self.db.executemany(
            "INSERT INTO mvt_snapshots "
            "(id, locale, content_sha256, retrieved_at) VALUES (?,?,?,?)",
            [
                (1, "nb", "old-digest", "2026-01-01T00:00:00Z"),
                (2, "nb", "new-digest", "2026-10-10T00:00:00Z"),
            ],
        )
        self.db.executemany(
            "INSERT INTO mvt_food_groups "
            "(snapshot_id, group_id, name) VALUES (?,?,?)",
            [
                (1, "poultry", "Synthetic poultry"),
                (2, "poultry", "Synthetic poultry"),
                (2, "dairy", "Synthetic dairy"),
                (2, "fish", "Synthetic fish"),
            ],
        )
        self.db.executemany(
            "INSERT INTO mvt_foods "
            "(snapshot_id, food_id, food_name, food_group_id, source_uri, "
            "edible_part_pct, energy_kcal) VALUES (?,?,?,?,?,?,?)",
            [
                (1, "OLD", "Old chicken", "poultry", "test:old", "100", "1"),
                (2, "CHICKEN", "Synthetic raw chicken", "poultry", "test:chicken", "100", "100"),
                (2, "SKYR", "Synthetic skyr", "dairy", "test:skyr", "100", "60"),
                (2, "SALMON", "Synthetic raw salmon", "fish", "test:salmon", "90", "200"),
                (2, "CHEESE", "Synthetic cheese", "dairy", "test:cheese", "100", "350"),
                (2, "MISSING", "Synthetic missing measurement", "poultry", "test:missing", None, None),
            ],
        )
        self.db.executemany(
            "INSERT INTO mvt_nutrients "
            "(snapshot_id, nutrient_id, name, unit) VALUES (?,?,?,?)",
            [(1, "Protein", "Protein", "g"), (2, "Protein", "Protein", "g")],
        )
        self.db.executemany(
            "INSERT INTO mvt_food_nutrients "
            "(snapshot_id, food_id, nutrient_id, quantity_per_100g, unit, source_id) "
            "VALUES (?,?,'Protein',?,'g','synthetic')",
            [
                (1, "OLD", "90"),
                (2, "CHICKEN", "20"),
                (2, "SKYR", "10"),
                (2, "SALMON", "18"),
                (2, "CHEESE", "25"),
            ],
        )

    def query(self, name):
        return self.db.execute((QUERIES / name).read_text(encoding="utf-8"))

    def test_reference_protein_per_100kcal_ranks_latest_snapshot_only(self):
        rows = self.query("mvt_protein_per_100kcal.sql").fetchall()
        self.assertEqual([r[0] for r in rows],
                         ["CHICKEN", "SKYR", "SALMON", "CHEESE"])
        self.assertEqual(rows[0][5], 20.0)
        self.assertEqual(rows[1][5], 16.67)
        self.assertEqual(rows[0][7], "2026-10-10T00:00:00Z")

    def test_reference_nutrition_per_100g_has_no_fixed_portion_target(self):
        rows = self.query("mvt_nutrition_per_100g.sql").fetchall()
        self.assertEqual([r[0] for r in rows],
                         ["CHEESE", "CHICKEN", "SALMON", "SKYR"])
        self.assertEqual(rows[0][3:5], (25.0, 350.0))
        self.assertEqual(rows[1][3:5], (20.0, 100.0))
        self.assertEqual(rows[2][3:5], (18.0, 200.0))
        self.assertEqual(rows[2][5], "90")

    def test_group_summary_keeps_missing_profiles_out_of_means(self):
        rows = self.query("mvt_reference_group_summary.sql").fetchall()
        indexed = {r[0]: r for r in rows}
        self.assertEqual(indexed["poultry"][2:5], (2, 1, 1))
        self.assertEqual(indexed["poultry"][5:7], (20.0, 100.0))
        self.assertEqual(indexed["dairy"][2:5], (2, 2, 2))
        self.assertEqual(indexed["dairy"][5:7], (17.5, 205.0))
        self.assertEqual(len(rows), 3)

    def test_coverage_audit_reports_missing_without_fabricated_zeros(self):
        rows = self.query("mvt_nutrition_coverage.sql").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "new-digest")
        self.assertEqual(rows[0][2:8], (5, 4, 1, 4, 1, 4))

    def test_kiwi_snapshot_quality_detects_ambiguous_unit(self):
        self.db.execute(
            "INSERT INTO weekly_pricecheck_snapshots "
            "(sha256, source_url, fetched_at, iso_year, iso_week, "
            "valid_through, original_html) VALUES (?,?,?,?,?,?,?)",
            ("kiwi-digest", "https://kiwi.no/dagligvarer/prissjekk",
             "2026-10-10T15:00:00Z", 2026, 41, "2026-10-11", "fixture"),
        )
        self.db.executemany(
            "INSERT INTO weekly_pricecheck_candidates "
            "(snapshot_sha256, row_no, chain_name, product_text, "
            "price_cents, price_basis, candidate_protein, review_state) "
            "VALUES ('kiwi-digest', ?, 'KIWI', ?, ?, ?, ?, 'source_only')",
            [
                (1, "Synthetic salmon CA300G", 27200, "unknown", 1),
                (2, "Synthetic skyr 1KG", 2000, "per_package", 1),
                (3, "Synthetic other fish", 31990, "per_kg", 0),
            ],
        )
        rows = self.query("kiwi_snapshot_quality.sql").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0:2], ("2026-W41", "2026-10-11"))
        self.assertEqual(rows[0][3:9], (3, 2, 1, 1, 1, 3))

    def test_empty_snapshot_has_no_fabricated_results(self):
        self.db.execute("DELETE FROM mvt_food_nutrients")
        self.db.execute("DELETE FROM mvt_foods")
        self.db.execute("DELETE FROM weekly_pricecheck_candidates")
        self.assertEqual(self.query("mvt_protein_per_100kcal.sql").fetchall(), [])
        self.assertEqual(self.query("mvt_nutrition_per_100g.sql").fetchall(), [])
        self.assertEqual(self.query("mvt_reference_group_summary.sql").fetchall(), [])
        self.assertEqual(self.query("mvt_nutrition_coverage.sql").fetchall(), [])
        self.assertEqual(self.query("kiwi_snapshot_quality.sql").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
