"""All sample foods/quantities here are synthetic offline test fixtures."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nutrivalue.providers.matvaretabellen import (
    MatvaretabellenError, fetch_payloads, import_payloads, urls_for,
)


def fake_documents():
    return {
        "food-groups": {"locale": "nb", "foodGroups": [
            {"foodGroupId": "3", "name": "Syntetisk kjøtt"},
            {"foodGroupId": "3.1", "name": "Syntetisk fjørfe", "parentId": "3"},
        ]},
        "nutrients": {"locale": "nb", "nutrients": [
            {"nutrientId": "Protein", "name": "Protein", "unit": "g",
             "euroFirId": "PROT", "decimalPrecision": 1},
            {"nutrientId": "Fett", "name": "Fett", "unit": "g"},
        ]},
        "sources": {"locale": "nb", "sources": [
            {"sourceId": "synthetic", "description": "Synthetic test data"},
        ]},
        "foods": {"locale": "nb", "foods": [
            {
                "foodId": "S.001", "foodName": "Syntetisk mat, rå",
                "foodGroupId": "3.1", "uri": "https://example.test/synthetic-raw",
                "ediblePart": {"percent": 78, "sourceId": "synthetic"},
                "calories": {"quantity": 120, "unit": "kcal"},
                "energy": {"quantity": 502, "unit": "kJ"},
                "constituents": [
                    {"nutrientId": "Protein", "quantity": 20.5, "unit": "g",
                     "sourceId": "synthetic"},
                    {"nutrientId": "Fett", "quantity": 1.2, "unit": "g",
                     "sourceId": "synthetic"},
                ],
            },
            {
                "foodId": "S.002", "foodName": "Syntetisk mat, kokt",
                "foodGroupId": "3.1", "uri": "https://example.test/synthetic-cooked",
                "ediblePart": {"percent": 100, "sourceId": "synthetic"},
                "calories": {"quantity": 140, "unit": "kcal"},
                "constituents": [
                    {"nutrientId": "Protein", "sourceId": "synthetic"},
                    {"nutrientId": "Fett", "quantity": 0, "unit": "g",
                     "sourceId": "synthetic"},
                    # Simulates the real upstream 06.178 BIOT source-only field:
                    # not present in the measured nutrient definitions.
                    {"nutrientId": "BIOT", "sourceId": "synthetic"},
                ],
            },
        ]},
    }


def payloads(documents):
    return {key: json.dumps(value, ensure_ascii=False).encode("utf-8")
            for key, value in documents.items()}


class MatvaretabellenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dbpath = Path(self.temporary.name) / "test.sqlite3"
        self.data = payloads(fake_documents())

    def test_relational_import_preserves_origins_and_no_prices(self):
        result = import_payloads(self.dbpath, self.data, retrieved_at="2026-10-10T11:00:00Z")
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["foods"], 2)
        self.assertEqual(result["measurements"], 3)
        self.assertEqual(result["unquantified_energy"], 0)
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mvt_foods").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mvt_sources").fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT energy_kcal, energy_kj FROM mvt_foods WHERE food_id='S.001'"
            ).fetchone(), ("120", "502"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mvt_endpoint_provenance").fetchone()[0], 4)
            r = db.execute(
                "SELECT protein_g_per_100g, edible_part_pct, protein_source_id, locale "
                "FROM mvt_latest_protein"
            ).fetchall()
            self.assertEqual(r, [(20.5, 78.0, "synthetic", "nb")])
            # Matvaretabellen cannot create a retail-price or matching record.
            names = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("price_observations", names)
            self.assertNotIn("products", names)

    def test_missing_value_is_not_zero_or_ranked(self):
        import_payloads(self.dbpath, self.data)
        with sqlite3.connect(self.dbpath) as db:
            self.assertIsNone(db.execute(
                "SELECT quantity_per_100g FROM mvt_food_nutrients "
                "WHERE food_id='S.002' AND nutrient_id='Protein'"
            ).fetchone())
            self.assertEqual(db.execute(
                "SELECT quantity_per_100g FROM mvt_food_nutrients "
                "WHERE food_id='S.002' AND nutrient_id='Fett'"
            ).fetchone(), ("0",))
            self.assertEqual(db.execute(
                "SELECT source_id FROM mvt_unquantified_nutrients "
                "WHERE food_id='S.002' AND nutrient_id='BIOT'"
            ).fetchone(), ("synthetic",))
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM mvt_food_nutrients WHERE nutrient_id='BIOT'"
            ).fetchone()[0], 0)

    def test_missing_energy_source_only_is_null_and_provenance_preserved(self):
        docs = fake_documents()
        # Reproduces official food 10.135 structure: source-only energy and
        # calories have no quantity/unit. This is missing, not zero kJ/kcal.
        docs["foods"]["foods"][0]["energy"] = {"sourceId": "synthetic"}
        docs["foods"]["foods"][0]["calories"] = {"sourceId": "synthetic"}
        result = import_payloads(self.dbpath, payloads(docs))
        self.assertEqual(result["unquantified_energy"], 2)
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute(
                "SELECT energy_kcal, energy_kj FROM mvt_foods WHERE food_id='S.001'"
            ).fetchone(), (None, None))
            rows = db.execute(
                "SELECT energy_unit, source_id FROM mvt_unquantified_energy "
                "WHERE food_id='S.001' ORDER BY energy_unit"
            ).fetchall()
            self.assertEqual(rows, [("kJ", "synthetic"), ("kcal", "synthetic")])
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM mvt_latest_protein").fetchone()[0], 1
            )

    def test_measured_energy_wrong_unit_is_rejected(self):
        docs = fake_documents()
        docs["foods"]["foods"][0]["calories"] = {"quantity": 120, "unit": "kJ"}
        with self.assertRaisesRegex(MatvaretabellenError, "calories unit must be kcal"):
            import_payloads(self.dbpath, payloads(docs))
        self.assertFalse(self.dbpath.exists())

    def test_measured_energy_missing_unit_is_rejected(self):
        docs = fake_documents()
        docs["foods"]["foods"][0]["energy"] = {"quantity": 502, "sourceId": "synthetic"}
        with self.assertRaisesRegex(MatvaretabellenError, "energy unit must be kJ"):
            import_payloads(self.dbpath, payloads(docs))
        self.assertFalse(self.dbpath.exists())

    def test_idempotent_same_content_and_new_changed_snapshot(self):
        first = import_payloads(self.dbpath, self.data, retrieved_at="2026-10-09T00:00:00Z")
        repeat = import_payloads(self.dbpath, self.data, retrieved_at="2026-10-10T00:00:00Z")
        self.assertEqual(repeat["status"], "unchanged")
        self.assertEqual(first["snapshot_id"], repeat["snapshot_id"])
        new_data = fake_documents()
        new_data["foods"]["foods"][0]["constituents"][0]["quantity"] = 21.0
        second = import_payloads(
            self.dbpath, payloads(new_data), retrieved_at="2026-10-11T00:00:00Z",
        )
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mvt_snapshots").fetchone()[0], 2)
            self.assertEqual(
                db.execute("SELECT protein_g_per_100g FROM mvt_latest_protein").fetchall(),
                [(21.0,)],
            )
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_unexpected_locale_fails_before_database_creation(self):
        data = fake_documents()
        data["foods"]["locale"] = "en"
        with self.assertRaises(MatvaretabellenError):
            import_payloads(self.dbpath, payloads(data))
        self.assertFalse(self.dbpath.exists())

    def test_unexpected_nutrient_unit_blocks_all_mutation(self):
        import_payloads(self.dbpath, self.data)
        data = fake_documents()
        data["foods"]["foods"][0]["constituents"][0]["unit"] = "mg"
        with self.assertRaises(MatvaretabellenError):
            import_payloads(self.dbpath, payloads(data))
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mvt_snapshots").fetchone()[0], 1)

    def test_unknown_measured_nutrient_fails_before_database_mutation(self):
        data = fake_documents()
        data["foods"]["foods"][1]["constituents"][-1]["quantity"] = 5
        data["foods"]["foods"][1]["constituents"][-1]["unit"] = "µg"
        with self.assertRaisesRegex(MatvaretabellenError, "unknown measured nutrient BIOT"):
            import_payloads(self.dbpath, payloads(data))
        self.assertFalse(self.dbpath.exists())

    def test_unquantified_source_only_records_are_idempotent(self):
        first = import_payloads(self.dbpath, self.data)
        again = import_payloads(self.dbpath, self.data)
        self.assertEqual(first["unquantified"], 2)  # Protein missing + BIOT missing
        self.assertEqual(again["unquantified"], 2)
        self.assertEqual(again["status"], "unchanged")
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM mvt_unquantified_nutrients").fetchone()[0], 2
            )

    def test_unknown_group_and_nonfinite_and_duplicates_rejected(self):
        for mode in ("group", "nan", "duplicate"):
            with self.subTest(mode=mode):
                data = fake_documents()
                if mode == "group":
                    data["foods"]["foods"][0]["foodGroupId"] = "404"
                if mode == "nan":
                    data["foods"]["foods"][0]["ediblePart"]["percent"] = "NaN"
                if mode == "duplicate":
                    data["foods"]["foods"].append(copy.deepcopy(data["foods"]["foods"][0]))
                with self.assertRaises(MatvaretabellenError):
                    import_payloads(self.dbpath, payloads(data))
                self.assertFalse(self.dbpath.exists())

    def test_offline_input_reads_only_four_named_files(self):
        folder = Path(self.temporary.name) / "raw"
        folder.mkdir()
        for kind, raw in self.data.items():
            (folder / (kind + ".json")).write_bytes(raw)
        with patch("nutrivalue.providers.matvaretabellen.urlopen",
                   side_effect=AssertionError("Network must not be called")):
            downloaded = fetch_payloads("nb", input_dir=folder)
        self.assertEqual(downloaded, self.data)
        self.assertIn("/api/nb/foods.json", urls_for("nb")["foods"])

    def test_missing_endpoint_rejected(self):
        missing = dict(self.data)
        missing.pop("sources")
        with self.assertRaises(MatvaretabellenError):
            import_payloads(self.dbpath, missing)
        self.assertFalse(self.dbpath.exists())


if __name__ == "__main__":
    unittest.main()
