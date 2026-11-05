"""TASK-041 synthetic-only owner-approved minimal GitHub review export tests."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nutrivalue.offer_review_export import (
    ExportError, assemble_review_export, write_private_export,
)
from nutrivalue.providers.tjek_offline import CHAINS

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/export_tjek_review.py"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-18T23:59:59+02:00"


class MinimalReviewExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        self.archive = self.temp / "archive"
        self.archive.mkdir()
        self.private = self.temp / "data" / "private"
        self.private.mkdir(parents=True)

    def create(self, dealer, *, price=99.99, declared=2, title="LAKSEFILET"):
        catalog_id = "synthetic-catalog-" + dealer
        value = {
            "fetched_at": "2026-10-16T11:00:00+02:00",
            "catalog": {
                "id": catalog_id, "dealer_id": dealer,
                "run_from": START, "run_till": END,
                "offer_count": declared,
            },
            "offers": [
                {
                    "id": f"SYNTHETIC-PRIVATE-ID-{n}",
                    "dealer_id": dealer,
                    "catalog_id": catalog_id,
                    "heading": title if n == 0 else "OSTEPOP CHEDDAR CHILI",
                    "description": "NORWEGIAN SOURCE DESCRIPTION",
                    "pricing": {"price": price, "currency": "NOK"},
                    "run_from": START, "run_till": END,
                }
                for n in (0, 1)
            ],
        }
        file = self.archive / (dealer + ".json")
        file.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return file

    def all_nine(self, **kwargs):
        return [self.create(dealer, **kwargs) for dealer in CHAINS]

    def test_complete_nine_chain_export_provenance_and_minimal_fields(self):
        files = self.all_nine(declared=3)
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        data, summary = assemble_review_export(self.archive)
        rows = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        self.assertEqual(len(rows), 18)
        self.assertEqual(summary["records"], 18)
        self.assertEqual(summary["chains"], 9)
        self.assertEqual(summary["source_declared_count"], 27)
        self.assertEqual(summary["source_count_shortfall"], 9)
        self.assertEqual(summary["by_rule_status"]["matched"], 18)
        self.assertEqual(rows[0]["chain"], "Bunnpris")
        self.assertEqual(rows[0]["n"], 1)
        self.assertEqual(rows[-1]["n"], 18)
        self.assertEqual(set(rows[0]), {
            "n", "chain", "heading", "description", "rule_status",
            "rule_category", "rule_reason", "rule_food_type",
        })
        self.assertEqual(rows[0]["rule_category"], "fish")
        self.assertEqual(rows[1]["rule_category"], "snacks")
        for fragment in (
            "SYNTHETIC-PRIVATE-ID", "synthetic-catalog-",
            '"price":', '"pricing":', '"dealer_id":', '"raw_payload":',
            '"ean":', '"fetched_at":', "99.99",
        ):
            self.assertNotIn(fragment, data.decode("utf-8"))
        self.assertNotIn("LAKSEFILET", json.dumps(summary))
        self.assertNotIn(str(self.archive), json.dumps(summary))
        self.assertEqual(summary["http_requests"], 0)
        self.assertEqual(summary["source_database_writes"], 0)
        self.assertEqual(before, {
            p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files
        })
        self.assertEqual(data, assemble_review_export(self.archive)[0])

    def test_missing_dealer_wrong_dealer_and_bad_payload_refused(self):
        files = self.all_nine()
        files[1].unlink()
        with self.assertRaisesRegex(ExportError, "nine selected"):
            assemble_review_export(self.archive)
        self.create("de79dm")
        object_file = self.archive / "de79dm.json"
        raw = json.loads(object_file.read_text())
        raw["catalog"]["dealer_id"] = "faa0Ym"
        for offer in raw["offers"]:
            offer["dealer_id"] = "faa0Ym"
        object_file.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ExportError, "mismatch"):
            assemble_review_export(self.archive)
        self.create("de79dm")
        self.create("faa0Ym")
        other = self.archive / "faa0Ym.json"
        raw = json.loads(other.read_text())
        raw["offers"][0]["pricing"]["currency"] = "EUR"
        other.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ExportError, "validation"):
            assemble_review_export(self.archive)

    def test_dry_run_cli_has_no_title_or_path_and_creates_no_files(self):
        self.all_nine()
        before = sorted(path.name for path in self.archive.iterdir())
        call = subprocess.run(
            [sys.executable, str(CLI), "--archive-dir", str(self.archive)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(call.returncode, 0, call.stderr)
        report = json.loads(call.stdout)
        self.assertEqual(report["records"], 18)
        self.assertFalse(report["private_export_created"])
        self.assertNotIn(str(self.archive), call.stdout + call.stderr)
        self.assertNotIn("LAKSEFILET", call.stdout + call.stderr)
        self.assertNotIn("SYNTHETIC-PRIVATE-ID", call.stdout + call.stderr)
        self.assertEqual(sorted(p.name for p in self.archive.iterdir()), before)

    def test_exclusive_mode_0600_and_never_overwrite_or_escape_private(self):
        self.all_nine()
        data, _ = assemble_review_export(self.archive)
        export_path = self.private / "task041-review.jsonl"
        self.assertFalse(export_path.exists())
        write_private_export(export_path, data, private_root=self.private)
        self.assertEqual(os.stat(export_path).st_mode & 0o777, 0o600)
        self.assertEqual(export_path.read_bytes(), data)
        with self.assertRaisesRegex(ExportError, "refusing overwrite"):
            write_private_export(export_path, data, private_root=self.private)
        public_path = self.temp / "public.jsonl"
        with self.assertRaisesRegex(ExportError, "inside data/private"):
            write_private_export(public_path, data, private_root=self.private)
        self.assertFalse(public_path.exists())

    def test_cli_export_requires_explicit_output_and_refuses_file_leak(self):
        self.all_nine()
        absent = subprocess.run(
            [sys.executable, str(CLI), "--archive-dir", str(self.archive),
             "--write"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(absent.returncode, 2)
        self.assertIn("--write and --output must be supplied together", absent.stderr)
        self.assertNotIn(str(self.archive), absent.stderr)
        self.assertNotIn("LAKSEFILET", absent.stderr)

    def test_explicit_export_is_private_and_report_contains_counts_only(self):
        self.all_nine()
        # Production CLI confines the export to ROOT/data/private; here a
        # synthetic test validates the pure writer's private-root contract.
        data, report = assemble_review_export(self.archive)
        out = self.private / "sample.jsonl"
        write_private_export(out, data, private_root=self.private)
        self.assertTrue(out.is_file())
        self.assertEqual(report["output_bytes"], len(out.read_bytes()))
        self.assertNotIn("OSTEPOP", json.dumps(report))
        with patch("nutrivalue.offer_review_export.MAX_EXPORT_BYTES", 10):
            with self.assertRaisesRegex(ExportError, "bounded"):
                assemble_review_export(self.archive)
