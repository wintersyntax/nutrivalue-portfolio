"""Synthetic/offline contract for Kassalapp private daily history staging."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.collect_kassalapp_history import (
    collect_history, stage_history, validate_eans,
)


EAN = "1234567890123"  # Entirely synthetic product ID.


def response():
    return {"data": [{
        "ean": EAN, "name": "Synthetic cottage cheese",
        "weight": None, "weight_unit": None,
        "stores": [
            {"store": "FAKE_A", "current_price": 39.90,
             "last_checked": "2026-10-10T07:00:00Z"},
            {"store": "FAKE_B", "current_price": 35.50,
             "last_checked": "2026-10-10T07:12:00Z"},
        ],
        "price_history": [
            {"price": 39.9, "date": "2026-10-10", "store": "FAKE_A"},
            {"price": 41.9, "date": "2026-10-09", "store": "FAKE_A"},
            {"price": 35.5, "date": "2026-10-10", "store": "FAKE_B"},
        ],
    }]}


class KassalappHistoryTests(unittest.TestCase):
    def test_first_import_preserves_exact_daily_store_price_in_ore(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "private.sqlite3"
            self.assertEqual(stage_history(response(), [EAN], path,
                             fetched_at="2026-10-10T18:00:00+00:00"), (1, 3, True))
            with sqlite3.connect(path) as db:
                rows = db.execute("""SELECT ean, store_code, price_day, price_ore,
                                     store_latest_checked_at, review_status
                                     FROM kassalapp_history_rows
                                     ORDER BY store_code, price_day DESC""").fetchall()
                self.assertEqual(rows, [
                    (EAN, "FAKE_A", "2026-10-10", 3990,
                     "2026-10-10T07:00:00Z", "source_only"),
                    (EAN, "FAKE_A", "2026-10-09", 4190,
                     "2026-10-10T07:00:00Z", "source_only"),
                    (EAN, "FAKE_B", "2026-10-10", 3550,
                     "2026-10-10T07:12:00Z", "source_only"),
                ])
                raw = db.execute("SELECT raw_response_json FROM kassalapp_history_snapshots").fetchone()[0]
                self.assertEqual(json.loads(raw), response())
                self.assertEqual(db.execute("SELECT count(*) FROM price_observations").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM nutrition_profiles").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM products").fetchone()[0], 0)

    def test_same_response_is_idempotent_and_changed_snapshot_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "private.sqlite3"
            self.assertEqual(stage_history(response(), [EAN], path), (1, 3, True))
            self.assertEqual(stage_history(response(), [EAN], path), (1, 3, False))
            changed = response()
            changed["data"][0]["price_history"][0]["price"] = 38.90
            self.assertEqual(stage_history(changed, [EAN], path), (1, 3, True))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM kassalapp_history_snapshots").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT count(*) FROM kassalapp_history_rows").fetchone()[0], 6)

    def test_invalid_values_fail_before_any_sql_write(self):
        variants = [
            lambda p: p["data"][0]["price_history"][0].update(price=0),
            lambda p: p["data"][0]["price_history"][0].update(price=0.001),
            lambda p: p["data"][0]["price_history"][0].update(date="bad"),
            lambda p: p["data"][0]["price_history"].append(
                dict(p["data"][0]["price_history"][0])),
            lambda p: p["data"][0].update(ean="not-requested"),
            lambda p: p["data"][0]["stores"][0].update(last_checked="not-a-date"),
        ]
        for change in variants:
            with self.subTest(change=str(change)), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "private.sqlite3"
                p = response()
                change(p)
                with self.assertRaises(ValueError):
                    stage_history(p, [EAN], path)
                self.assertFalse(path.exists())

    def test_empty_price_history_keeps_private_source_snapshot_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "private.sqlite3"
            p = response()
            p["data"][0]["price_history"] = []
            p["data"][0]["stores"] = []
            self.assertEqual(stage_history(p, [EAN], path), (1, 0, True))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM kassalapp_history_rows").fetchone()[0], 0)

    def test_live_call_contract_bounded_once_with_no_verified_promotion(self):
        calls = []
        class Client:
            def bulk_price_history(self, eans, *, days, aggregation):
                calls.append((eans, days, aggregation))
                return response()
        with tempfile.TemporaryDirectory() as d:
            with patch("scripts.collect_kassalapp_history.KassalappClient.from_environment",
                       return_value=Client()):
                count = collect_history([EAN], Path(d) / "private.sqlite3", days=30)
        self.assertEqual(count, (1, 3, True))
        self.assertEqual(calls, [([EAN], 30, "min")])

    def test_duplicate_ean_invalid_days_and_boolean_values_rejected(self):
        for eans, days in [([EAN, EAN], 30), (["x"], 30), ([EAN], 91),
                           ([EAN], True), ([], 30)]:
            with self.subTest(eans=eans, days=days), self.assertRaises(ValueError):
                validate_eans(eans, days)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                stage_history(response(), [EAN], Path(d) / "x.sqlite3", aggregation="avg")


if __name__ == "__main__":
    unittest.main()
