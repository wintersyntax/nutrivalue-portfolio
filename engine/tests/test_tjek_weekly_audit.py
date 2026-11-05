"""TASK-030: deterministic NO NETWORK weekly saved-Tjek coverage fixtures."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from nutrivalue.providers.tjek_offline import CHAINS
from nutrivalue.providers.tjek_weekly_audit import (
    WeeklyAuditError, audit_saved_week,
)

ROOT = Path(__file__).resolve().parents[1]
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-19T00:00:00+02:00"
NOW = "2026-10-15T12:00:00+02:00"
FETCHED = "2026-10-13T12:00:00+02:00"


def entry_for(dealer):
    catalog_id = "synthetic-cat-" + dealer
    return {
        "catalog": {
            "id": catalog_id, "dealer_id": dealer,
            "label": "SYNTHETIC week 42",
            "run_from": START, "run_till": END, "offer_count": 1,
        },
        "offers": [{
            "id": "synthetic-offer-" + dealer, "catalog_id": catalog_id,
            "dealer_id": dealer, "heading": "SYNTHETIC CHICKEN OFFER",
            "description": "pr. kg 100,00",
            "run_from": START, "run_till": END,
            "pricing": {"price": 20, "currency": "NOK"},
            "quantity": {
                "unit": {"symbol": "g"},
                "size": {"from": 200, "to": 200},
            },
        }],
    }


class WeeklyAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def save(self, dealer, *, entry=None, filename=None, fetched_at=FETCHED):
        envelope = {
            "fetched_at": fetched_at,
            "entries": [entry if entry is not None else entry_for(dealer)],
        }
        file = self.folder / (filename or f"{dealer}.json")
        file.write_text(json.dumps(envelope), encoding="utf-8")
        return file

    def all_nine(self):
        return [self.save(dealer) for dealer in CHAINS]

    def test_all_nine_source_declared_current_are_candidates_not_verified(self):
        result = audit_saved_week(self.all_nine(), as_of=NOW)
        self.assertEqual(result["batch_status"], "candidate_complete")
        self.assertEqual(result["candidate_chains"], 9)
        self.assertEqual(result["required_chains"], 9)
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["mutations"], 0)
        self.assertTrue(all(item["declared_counts_match"] for item in result["chains"]))
        self.assertTrue(all(item["snapshot_sha256"] for item in result["chains"]))
        self.assertEqual({item["status"] for item in result["chains"]}, {"candidate_current"})
        self.assertIn("not independently verified", result["disclaimer"])

    def test_missing_eight_chains_are_null_not_fabricated_zeros(self):
        dealer = next(iter(CHAINS))
        result = audit_saved_week([self.save(dealer)], as_of=NOW)
        self.assertEqual(result["batch_status"], "partial_or_unproven")
        self.assertEqual(result["candidate_chains"], 1)
        self.assertEqual(sum(r["status"] == "missing" for r in result["chains"]), 8)
        self.assertTrue(all(r["offers"] is None for r in result["chains"] if r["status"] == "missing"))

    def test_declared_offer_count_mismatch_blocks_candidate(self):
        dealer = next(iter(CHAINS))
        entry = entry_for(dealer)
        entry["catalog"]["offer_count"] = 2
        result = audit_saved_week([self.save(dealer, entry=entry)], as_of=NOW)
        self.assertEqual(result["chains"][0]["status"], "count_mismatch")
        self.assertFalse(result["chains"][0]["declared_counts_match"])

    def test_missing_declared_offer_count_is_not_complete(self):
        dealer = next(iter(CHAINS))
        entry = entry_for(dealer)
        del entry["catalog"]["offer_count"]
        result = audit_saved_week([self.save(dealer, entry=entry)], as_of=NOW)
        self.assertEqual(result["chains"][0]["status"], "unknown_source_counts")

    def test_expired_and_future_catalogues_cannot_be_called_current(self):
        dealer = next(iter(CHAINS))
        for start, end in (
            ("2026-10-01T00:00:00+02:00", "2026-10-08T00:00:00+02:00"),
            ("2026-10-20T00:00:00+02:00", "2026-10-27T00:00:00+01:00"),
        ):
            with self.subTest(start=start):
                entry = entry_for(dealer)
                entry["catalog"].update(run_from=start, run_till=end)
                entry["offers"][0].update(run_from=start, run_till=end)
                p = self.save(dealer, entry=entry)
                result = audit_saved_week([p], as_of=NOW)
                self.assertEqual(result["chains"][0]["status"], "catalog_out_of_window")

    def test_current_catalog_but_expired_offer_is_not_current(self):
        dealer = next(iter(CHAINS))
        entry = entry_for(dealer)
        entry["offers"][0]["run_till"] = "2026-10-14T00:00:00+02:00"
        result = audit_saved_week([self.save(dealer, entry=entry)], as_of=NOW)
        self.assertEqual(result["chains"][0]["status"], "no_current_offers")

    def test_later_capture_cannot_prove_earlier_week(self):
        dealer = next(iter(CHAINS))
        file = self.save(dealer, fetched_at="2026-10-16T00:00:00+02:00")
        result = audit_saved_week([file], as_of=NOW)
        self.assertEqual(result["chains"][0]["status"], "fetched_after_as_of")

    def test_duplicate_dealer_files_and_mixed_dealers_rejected(self):
        first, second = list(CHAINS)[:2]
        p = self.save(first)
        with self.assertRaisesRegex(WeeklyAuditError, "duplicate chain"):
            audit_saved_week([p, p], as_of=NOW)
        envelope = {
            "fetched_at": FETCHED,
            "entries": [entry_for(first), entry_for(second)],
        }
        mixed = self.folder / "mixed.json"
        mixed.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaisesRegex(WeeklyAuditError, "one chain only"):
            audit_saved_week([mixed], as_of=NOW)

    def test_bad_input_fail_closed_before_success_decision(self):
        dealer = next(iter(CHAINS))
        entry = entry_for(dealer)
        entry["offers"][0]["dealer_id"] = "not-a-known-dealer"
        with self.assertRaisesRegex(WeeklyAuditError, "invalid saved snapshot"):
            audit_saved_week([self.save(dealer, entry=entry)], as_of=NOW)
        with self.assertRaisesRegex(WeeklyAuditError, "unreadable"):
            audit_saved_week([self.folder / "absent.json"], as_of=NOW)
        with self.assertRaisesRegex(WeeklyAuditError, "1..9"):
            audit_saved_week([], as_of=NOW)
        with self.assertRaisesRegex(WeeklyAuditError, "1..9"):
            audit_saved_week([self.save(dealer)] * 10, as_of=NOW)

    def test_timestamp_must_be_timezone_aware(self):
        path = self.save(next(iter(CHAINS)))
        for value in ("2026-10-15", "2026-10-15T12:00:00", "garbage"):
            with self.subTest(value=value), self.assertRaises(WeeklyAuditError):
                audit_saved_week([path], as_of=value)

    def test_read_only_and_report_never_prints_source_products_prices_paths(self):
        dealer = next(iter(CHAINS))
        p = self.save(dealer)
        before = p.read_bytes()
        result = audit_saved_week([p], as_of=NOW)
        report_text = json.dumps(result)
        self.assertNotIn("CHICKEN", report_text)
        self.assertNotIn("100,00", report_text)
        self.assertNotIn(str(self.folder), report_text)
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), [p.name])

    def test_cli_partial_uses_exit_2_and_no_data_dump(self):
        path = self.save(next(iter(CHAINS)))
        command = [sys.executable, str(ROOT / "scripts/audit_tjek_weekly_offline.py"),
                   "--as-of", NOW, "--input", str(path)]
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertIn("PARTIAL / UNPROVEN", process.stdout)
        self.assertIn('"network_requests": 0', process.stdout)
        self.assertIn('"mutations": 0', process.stdout)
        self.assertNotIn("SYNTHETIC CHICKEN OFFER", process.stdout)
        self.assertNotIn("100,00", process.stdout)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), [path.name])

    def test_cli_complete_synthetic_nine_uses_exit_0(self):
        paths = self.all_nine()
        command = [sys.executable, str(ROOT / "scripts/audit_tjek_weekly_offline.py"),
                   "--as-of", NOW]
        for p in paths:
            command.extend(["--input", str(p)])
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("CANDIDATE ONLY", process.stdout)
        self.assertIn('"candidate_chains": 9', process.stdout)


if __name__ == "__main__":
    unittest.main()
