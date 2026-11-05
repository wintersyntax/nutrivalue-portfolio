import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript((ROOT / "analytics/schema.sql").read_text())
        self.db.execute("INSERT INTO stores(id, name) VALUES (1, 'Example Store')")
        self.db.execute(
            "INSERT INTO products(id, canonical_name, category) "
            "VALUES (1, 'Synthetic chicken product', 'poultry')"
        )
        self.db.execute(
            "INSERT INTO nutrition_profiles "
            "(product_id, protein_per_100g, measurement_basis, profile_type, "
            "source_name, source_ref, captured_at) "
            "VALUES (1, 18, 'as_sold', 'generic_estimate', 'test', "
            "'test-fixture:synthetic-profile', '2026-10-10')"
        )

    def tearDown(self):
        self.db.close()

    def insert_price(self, status="verified", until="2099-12-31"):
        self.db.execute(
            "INSERT INTO price_observations "
            "(product_id, store_id, price_nok, package_net_edible_g, "
            "observed_at, valid_from, valid_until, source_name, source_ref, "
            "verification_status) VALUES (1, 1, 60, 400, '2026-10-10', "
            "'2020-01-01', ?, 'test', 'test-fixture:synthetic-price', ?)",
            (until, status),
        )

    def test_sql_ranking_hypothetical_fixture(self):
        self.insert_price()
        rows = self.db.execute(
            (ROOT / "analytics/queries/best_protein_value.sql").read_text()
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], 72.0)
        self.assertEqual(rows[0][6], 0.83)

    def test_unverified_or_expired_prices_excluded(self):
        self.insert_price(status="demo")
        self.insert_price(status="verified", until="2020-01-01")
        rows = self.db.execute(
            (ROOT / "analytics/queries/best_protein_value.sql").read_text()
        ).fetchall()
        self.assertEqual(rows, [])

    def test_negative_prices_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO price_observations "
                "(product_id, store_id, price_nok, package_net_edible_g, "
                "observed_at, valid_from, valid_until, source_name, source_ref, "
                "verification_status) VALUES "
                "(1, 1, -60, 400, '2026-10-10', '2026-10-10', '2026-10-17', "
                "'test', 'fixture', 'verified')"
            )


if __name__ == "__main__":
    unittest.main()
