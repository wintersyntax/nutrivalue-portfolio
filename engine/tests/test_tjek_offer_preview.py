"""TASK-031 deterministic synthetic tests for source-only private offer preview."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from nutrivalue.providers.tjek_offline import parse_snapshot, stage_snapshot
from nutrivalue.providers.tjek_offer_preview import (
    TjekPreviewError, format_table, preview_offers,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "analytics/tjek_offer_staging.sql"
AS_OF = "2026-10-15T12:00:00+02:00"
FETCHED = "2026-10-11T18:00:00+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-17T23:59:59+02:00"


def make_entry(*, dealer="faa0Ym", name="SYNTHETIC CHICKEN", desc="199,75 pr. kg",
               declared=1, price=79.90):
    cat = "synthetic-week42-" + dealer
    return {
        "catalog": {
            "id": cat, "dealer_id": dealer, "label": "SYNTHETIC 42",
            "run_from": START, "run_till": END, "offer_count": declared,
        },
        "offers": [{
            "id": "synthetic-offer", "catalog_id": cat, "dealer_id": dealer,
            "heading": name, "description": desc,
            "pricing": {"currency": "NOK", "price": price},
            "quantity": {
                "unit": {"symbol": "g"},
                "size": {"from": 100, "to": 100},
                "pieces": {"from": 4, "to": 4},
            },
            "run_from": START, "run_till": END,
        }],
    }


class PrivateTjekPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.db = self.dir / "tjek-private.sqlite3"

    def stage(self, entry=None, fetched=FETCHED):
        entries = [entry if entry is not None else make_entry()]
        body = {"fetched_at": fetched, "entries": entries}
        snapshot = parse_snapshot(
            json.dumps(body, sort_keys=True).encode("utf-8"), "synthetic.json")
        stage_snapshot(snapshot, self.db, SCHEMA)
        return snapshot

    def row(self, **kwargs):
        self.stage(make_entry(**kwargs))
        out = preview_offers(self.db, as_of=AS_OF)
        self.assertEqual(out["total"], 1)
        return out["rows"][0]

    def test_first_realistic_source_display_has_exact_price_pack_and_printed_rate(self):
        row = self.row()
        self.assertEqual(row["chain"], "REMA 1000")
        self.assertEqual(row["advertised_price"], "79,90 NOK")
        self.assertEqual(row["package"], "4 x 100 g")
        self.assertEqual(row["unit_rate"], "199,75 NOK/kg")
        self.assertEqual(row["rate_check"], "source_print_consistent")
        self.assertEqual(row["window"], "current")
        self.assertEqual(row["coverage"], "declared_match")
        self.assertEqual(row["source"], "source_only")
        text = format_table(preview_offers(self.db, as_of=AS_OF))
        self.assertIn("SYNTHETIC CHICKEN", text)
        self.assertIn("79,90 NOK", text)
        self.assertIn("source_only", text)

    def test_unchecked_calculated_rate_is_withheld(self):
        row = self.row(desc="No printed source rate")
        self.assertEqual(row["unit_rate"], "withheld")
        self.assertEqual(row["rate_check"], "calculated_unchecked")

    def test_mismatched_printed_rate_is_withheld(self):
        row = self.row(desc="99,00 pr. kg")
        self.assertEqual(row["unit_rate"], "withheld")
        self.assertEqual(row["rate_check"], "source_rate_mismatch")

    def test_member_multibuy_and_pant_withhold_rates(self):
        for description, expected in [
            ("199,75 pr. kg Medlemspris", "member"),
            ("2 for 159,80; 199,75 pr. kg", "multi-buy"),
            ("199,75 pr. kg Pant", "pant"),
        ]:
            with self.subTest(description=description):
                row = self.row(desc=description)
                self.assertEqual(row["unit_rate"], "withheld")
                self.assertEqual(row["conditions"], expected)
                self.db.unlink()

    def test_missing_and_ranged_quantity_are_not_invented(self):
        for typ in ["missing", "ranged"]:
            with self.subTest(typ=typ):
                entry = make_entry()
                if typ == "missing":
                    entry["offers"][0].pop("quantity")
                else:
                    entry["offers"][0]["quantity"]["size"]["to"] = 150
                self.stage(entry)
                result = preview_offers(self.db, as_of=AS_OF)
                self.assertEqual(result["rows"][0]["package"], "unknown")
                self.assertEqual(result["rows"][0]["unit_rate"], "withheld")
                self.db.unlink()

    def test_catalog_incomplete_and_unknown_counts_labelled(self):
        for declared, expected in [(4, "partial (1/4)"), (None, "unproven (1/?)")]:
            with self.subTest(declared=declared):
                self.stage(make_entry(declared=declared))
                out = preview_offers(self.db, as_of=AS_OF)
                self.assertEqual(out["rows"][0]["coverage"], expected)
                self.db.unlink()

    def test_active_window_excludes_expired_future_and_late_capture(self):
        self.stage()
        expired = preview_offers(self.db, as_of="2026-10-20T12:00:00+02:00")
        self.assertEqual(expired["total"], 0)
        self.assertIn("No saved offers", format_table(expired))
        self.assertEqual(preview_offers(
            self.db, as_of="2026-10-20T12:00:00+02:00",
            include_inactive=True)["rows"][0]["window"], "expired")
        self.assertEqual(preview_offers(
            self.db, as_of="2026-10-11T20:00:00+02:00",
            include_inactive=True)["rows"][0]["window"], "future")
        self.assertEqual(preview_offers(
            self.db, as_of="2026-10-10T12:00:00+02:00",
            include_inactive=True)["rows"][0]["window"], "fetched_later")

    def test_expired_catalog_overrides_still_valid_offer(self):
        entry = make_entry()
        entry["catalog"]["run_till"] = "2026-10-14T00:00:00+02:00"
        self.stage(entry)
        self.assertEqual(preview_offers(self.db, as_of=AS_OF)["total"], 0)
        self.assertEqual(preview_offers(
            self.db, as_of=AS_OF, include_inactive=True)["rows"][0]["window"], "expired")

    def test_latest_snapshot_per_source_identity_without_deleting_old_history(self):
        first = self.stage()
        newer = make_entry(name="SYNTHETIC CHICKEN UPDATED", price=89.90, desc="No rate")
        self.stage(newer, fetched="2026-10-14T18:00:00+02:00")
        out = preview_offers(self.db, as_of=AS_OF)
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["rows"][0]["product"], "SYNTHETIC CHICKEN UPDATED")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tjek_snapshots").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tjek_offers").fetchone()[0], 2)

    def test_private_db_is_never_modified_by_preview(self):
        self.stage()
        before = self.db.read_bytes()
        report = preview_offers(self.db, as_of=AS_OF)
        self.assertEqual((report["database_writes"], report["network_requests"]), (0, 0))
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(sorted(path.name for path in self.dir.iterdir()), [self.db.name])

    def test_rejects_missing_db_and_unexpected_schema_without_creating_one(self):
        missing = self.dir / "not-created.sqlite3"
        with self.assertRaisesRegex(TjekPreviewError, "missing"):
            preview_offers(missing, as_of=AS_OF)
        self.assertFalse(missing.exists())
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE other_table (id INTEGER)")
        with self.assertRaisesRegex(TjekPreviewError, "schema"):
            preview_offers(self.db, as_of=AS_OF)

    def test_time_and_limit_arguments_rejected(self):
        self.stage()
        for bad in ("2026-10-15", "2026-10-15T12:00:00", "not-a-time"):
            with self.subTest(bad=bad), self.assertRaises(TjekPreviewError):
                preview_offers(self.db, as_of=bad)
        for bad in (0, -1, 101, 1.5, True):
            with self.subTest(bad=bad), self.assertRaises(TjekPreviewError):
                preview_offers(self.db, as_of=AS_OF, limit=bad)

    def test_missing_headline_price_does_not_invent_amount_or_rate(self):
        row = self.row(price=None)
        self.assertEqual(row["advertised_price"], "unknown")
        self.assertEqual(row["unit_rate"], "withheld")

    def test_cli_shows_only_private_source_only_preview_and_honors_limit(self):
        self.stage()
        command = [sys.executable, str(ROOT / "scripts/preview_tjek_offers.py"),
                   "--db", str(self.db), "--as-of", AS_OF, "--limit", "1"]
        before = self.db.read_bytes()
        r = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SYNTHETIC CHICKEN", r.stdout)
        self.assertIn("79,90 NOK", r.stdout)
        self.assertIn("No API requests. No database writes", r.stdout)
        self.assertNotIn("verified local", r.stdout.lower().replace("not verified local", ""))
        self.assertEqual(before, self.db.read_bytes())

    def test_cli_fail_closed_for_missing_database(self):
        command = [sys.executable, str(ROOT / "scripts/preview_tjek_offers.py"),
                   "--db", str(self.dir / "missing.sqlite3"),
                   "--as-of", AS_OF]
        r = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 1)
        self.assertIn("RED", r.stderr)
        self.assertEqual(list(self.dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
