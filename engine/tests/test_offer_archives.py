"""TASK-041 offline saved-file audit regression tests (synthetic-only)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nutrivalue.offer_category_audit import (
    OfferCategoryAuditError, audit_saved_archives,
)
from nutrivalue.providers.tjek_offline import CHAINS

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_offer_categories.py"
NOW = "2026-10-17T12:00:00+02:00"
FETCHED = "2026-10-16T10:00:00+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-18T23:59:59+02:00"


class SavedArchiveAuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def archive(self, dealer, heading="LAKS", *, declared=1, valid=True):
        catalog_id = "synthetic-catalog-" + dealer
        body = {
            "fetched_at": FETCHED,
            "catalog": {
                "id": catalog_id,
                "dealer_id": dealer,
                "label": "SYNTHETIC",
                "run_from": START,
                "run_till": END,
                "offer_count": declared,
            },
            "offers": [{
                "id": "synthetic-offer",
                "catalog_id": catalog_id,
                "dealer_id": dealer,
                "heading": heading,
                "description": "99,80 pr. kg",
                "pricing": {"currency": "NOK" if valid else "EUR",
                            "price": 49.90},
                "quantity": {"unit": {"symbol": "g"},
                             "size": {"from": 500, "to": 500}},
                "run_from": START,
                "run_till": END,
            }],
        }
        path = self.directory / f"{dealer}.json"
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return path

    def test_nine_saved_chains_aggregate_without_any_sqlite_or_source_mutation(self):
        paths = [self.archive(dealer, "TORSK" if index % 2 else "SKYR")
                 for index, dealer in enumerate(CHAINS)]
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        result = audit_saved_archives(paths, as_of=NOW)
        self.assertEqual(result["archive_inputs"], 9)
        self.assertEqual(result["distinct_observed_offers"], 9)
        self.assertEqual(result["active_offers"], 9)
        self.assertEqual(result["status"]["matched"], 9)
        self.assertEqual(result["by_category"]["fish"], 4)
        self.assertEqual(result["by_category"]["dairy"], 5)
        self.assertEqual(result["potential_food_key_types"], 9)
        self.assertEqual(result["unconditioned_source_consistent_kg_rate"], 9)
        self.assertEqual(result["missing_selected_chains"], [])
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["database_writes"], 0)
        self.assertTrue(all(item["source_status"] == "source_declared_match"
                            for item in result["by_chain_source_completeness"].values()))
        self.assertEqual(hashes, {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
        })
        output = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("synthetic-offer", output)
        self.assertNotIn("synthetic-catalog", output)
        self.assertNotIn(str(self.directory), output)
        self.assertNotIn("49.9", output)
        self.assertNotIn("SKYR", output)

    def test_declared_count_shortfall_is_reported_not_filled_in(self):
        path = self.archive("faa0Ym", "LAKSEFILET", declared=9)
        report = audit_saved_archives([path], as_of=NOW)
        self.assertEqual(report["distinct_observed_offers"], 1)
        self.assertEqual(report["archive_inputs"], 1)
        self.assertEqual(len(report["missing_selected_chains"]), 8)
        self.assertEqual(
            report["by_chain_source_completeness"]["REMA 1000"],
            {"catalogs": 1, "observed": 1, "declared": 9,
             "source_status": "source_count_mismatch"})
        self.assertNotIn("faa0Ym", json.dumps(report))
        self.assertEqual(report["status"]["matched"], 1)

    def test_unknown_declared_source_counts_stay_unknown(self):
        path = self.archive("faa0Ym", declared=None)
        report = audit_saved_archives([path], as_of=NOW)
        self.assertIsNone(
            report["by_chain_source_completeness"]["REMA 1000"]["declared"])
        self.assertEqual(
            report["by_chain_source_completeness"]["REMA 1000"]["source_status"],
            "unknown_source_counts")

    def test_duplicate_chain_and_invalid_snapshot_rejected(self):
        path = self.archive("faa0Ym")
        second = self.directory / "other.json"
        second.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(OfferCategoryAuditError, "same chain"):
            audit_saved_archives([path, second], as_of=NOW)
        wrong_currency = self.archive("257bxm", valid=False)
        with self.assertRaisesRegex(OfferCategoryAuditError, "offline validation"):
            audit_saved_archives([wrong_currency], as_of=NOW)
        with self.assertRaisesRegex(OfferCategoryAuditError, "one to nine"):
            audit_saved_archives([], as_of=NOW)

    def test_missing_invalid_timestamp_and_size_rejected_without_data_leak(self):
        with self.assertRaisesRegex(OfferCategoryAuditError, "missing/unreadable"):
            audit_saved_archives([self.directory / "not-present.json"], as_of=NOW)
        path = self.archive("faa0Ym")
        with self.assertRaises(OfferCategoryAuditError):
            audit_saved_archives([path], as_of="2026-10-17")
        with patch("nutrivalue.offer_category_audit.MAX_FILE_BYTES", 20):
            with self.assertRaisesRegex(OfferCategoryAuditError, "offline validation"):
                audit_saved_archives([path], as_of=NOW)

    def test_cli_archive_directory_ignores_manifest_and_only_emits_counts(self):
        self.archive("faa0Ym", "SYNTHETIC-PRIVATE-SOURCE-HEADING")
        (self.directory / "manifest.json").write_text(
            '{"private_source_title": "SECRET-DO-NOT-EMIT"}', encoding="utf-8")
        command = [sys.executable, str(SCRIPT), "--archive-dir",
                   str(self.directory), "--as-of", NOW]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["distinct_observed_offers"], 1)
        self.assertEqual(report["status"]["unmapped"], 1)
        self.assertEqual(report["archive_inputs"], 1)
        self.assertEqual(report["database_writes"], 0)
        self.assertEqual(report["network_requests"], 0)
        for secret in ("SECRET-DO-NOT-EMIT", "SYNTHETIC-PRIVATE-SOURCE-HEADING",
                       str(self.directory), "synthetic-catalog"):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_cli_explicit_inputs_and_missing_archive_directory(self):
        paths = [self.archive("faa0Ym", "LAKS"),
                 self.archive("257bxm", "RØKT LAKS")]
        success = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", *map(str, paths),
             "--as-of", NOW],
            text=True, capture_output=True, check=False)
        self.assertEqual(success.returncode, 0, success.stderr)
        out = json.loads(success.stdout)
        self.assertEqual(out["distinct_observed_offers"], 2)
        self.assertEqual(out["status"]["needs_review"], 1)
        missing = subprocess.run(
            [sys.executable, str(SCRIPT), "--archive-dir",
             str(self.directory / "not-present"), "--as-of", NOW],
            text=True, capture_output=True, check=False)
        self.assertEqual(missing.returncode, 2)
        self.assertNotIn(str(self.directory), missing.stderr)
