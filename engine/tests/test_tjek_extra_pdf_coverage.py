"""TASK-032: synthetic zero-HTTP/zero-OCR Extra source count vs PDF audit."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from nutrivalue.providers.tjek_extra_pdf_coverage import (
    audit_saved_extra_pdf, pdf_coverage,
)
from nutrivalue.providers.tjek_weekly_collect import CollectionError

AT = "2026-10-16T02:46:43+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-19T00:00:00+02:00"
CID = "W8l7eqAB"
PDF_TEXT = (
    "Extra ØST\nPrisene gjelder uke 42 - 2026\n"
    "SYNTHETIC FOOD ONE 29 90\n"
    "UNLISTED CHOCOLATE\n29 90\n"
)


def catalog(count=118):
    return {
        "id": CID, "dealer_id": "80742m", "label": "",
        "run_from": START, "run_till": END, "publish": START,
        "offer_count": count, "all_stores": False,
    }


def offer(name="SYNTHETIC FOOD ONE", ident="syn1", price=29.90):
    return {
        "id": ident, "dealer_id": "80742m", "catalog_id": CID,
        "heading": name, "description": "SYNTHETIC food 500 g",
        "pricing": {"price": price, "currency": "NOK"},
        "quantity": {"unit": {"symbol": "g"}, "size": {"from": 500, "to": 500},
                     "pieces": {"from": 1, "to": 1}},
        "run_from": START, "run_till": END,
    }


class CoverageTests(unittest.TestCase):
    def test_118_declared_103_available_do_not_create_15_phantom_products(self):
        # Synthetic counts: only ONE source row here; the 103 real-world
        # source rows remain owner-private and never enter Git fixtures.
        result = pdf_coverage(pdf_text=PDF_TEXT, as_of=AT,
                              catalog=catalog(), offers=[offer()])
        self.assertEqual(result["declared_catalog_offer_count"], 118)
        self.assertEqual(result["available_api_offer_rows"], 1)
        self.assertEqual(result["unresolved_declared_shortfall"], 117)
        self.assertEqual(result["verified_new_missing_offers"], 0)
        self.assertEqual(result["matched_tjek_rows_by_heuristic"], 1)
        self.assertGreaterEqual(result["pdf_only_possible_heading_candidates"], 1)

    def test_pdf_missing_source_heading_stays_unverified(self):
        r = pdf_coverage(pdf_text=PDF_TEXT, as_of=AT,
                         catalog=catalog(2),
                         offers=[offer(), offer("OTHER SOURCED FOOD", "syn2")])
        self.assertEqual(r["unmatched_tjek_rows_by_heuristic"], 1)
        self.assertEqual(r["unresolved_declared_shortfall"], 0)
        self.assertEqual(r["verified_new_missing_offers"], 0)

    def test_pdf_wrong_week_or_foreign_catalog_is_rejected(self):
        with self.assertRaises(CollectionError):
            pdf_coverage(pdf_text=PDF_TEXT.replace("42", "41"), as_of=AT,
                         catalog=catalog(), offers=[offer()])
        with self.assertRaises(CollectionError):
            pdf_coverage(pdf_text=PDF_TEXT, as_of=AT,
                         catalog=dict(catalog(), dealer_id="wrong"), offers=[offer()])
        with self.assertRaises(CollectionError):
            pdf_coverage(pdf_text=PDF_TEXT, as_of=AT,
                         catalog=catalog(), offers=[offer(ident="same"), offer(ident="same")])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "private"
        self.root.mkdir()
        self.folder = self.root / "2026-W42" / "extra-compare-20261016T004643Z"
        self.folder.mkdir(parents=True)
        self.source = self.folder / f"{CID}.json"
        envelope = {"fetched_at": AT, "entries": [{
            "catalog": catalog(count=118), "offers": [offer()]
        }]}
        raw = json.dumps(envelope).encode("utf-8")
        self.source.write_bytes(raw)
        (self.folder / "manifest.json").write_text(json.dumps({
            "week": "2026-W42", "run_directory": self.folder.name,
            "halted_reason": None,
            "attempts": [{
                "catalog_id": CID, "status": "source_saved",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "available_offers": 1, "shortfall": 117,
            }],
        }))
        (self.folder / "coop-ost.pdf").write_bytes(b"%PDF-synthetic")
        self.geo = self.root / "2026-W42" / "extra-api-investigation"
        self.geo.mkdir()
        self.geo_file = self.geo / "extra-geo-resolution-v1.json"
        self.geo_file.write_text(json.dumps({
            "status": "geo_oslo_catalog_candidate", "http_requests": 0,
            "oslo_candidate_catalog_id": CID,
        }))

    def test_private_geo_selected_pdf_audit_no_network_and_immutable(self):
        old = self.source.read_bytes()
        report = audit_saved_extra_pdf(
            private_root=self.root, compare_dir=self.folder,
            geo_report_path=self.geo_file, as_of=AT,
            extract_text=lambda path: PDF_TEXT,
        )
        self.assertEqual(report["catalog_id"], CID)
        self.assertEqual(report["http_requests"], 0)
        self.assertEqual(report["available_api_offer_rows"], 1)
        self.assertEqual(report["declared_minus_available"], 117)
        self.assertTrue((self.folder / "extra-pdf-coverage-v1.json").exists())
        self.assertEqual(self.source.read_bytes(), old)
        self.assertEqual(
            (self.folder / "extra-pdf-coverage-v1.json").stat().st_mode & 0o777,
            0o600)
        with self.assertRaisesRegex(CollectionError, "already exists"):
            audit_saved_extra_pdf(
                private_root=self.root, compare_dir=self.folder,
                geo_report_path=self.geo_file, as_of=AT,
                extract_text=lambda path: PDF_TEXT,
            )

    def test_missing_or_unproven_geo_identity_blocks_pdf_selected_audit(self):
        self.geo_file.write_text(json.dumps({
            "status": "geo_unresolved", "http_requests": 0,
            "oslo_candidate_catalog_id": None,
        }))
        with self.assertRaisesRegex(CollectionError, "lacks successful"):
            audit_saved_extra_pdf(
                private_root=self.root, compare_dir=self.folder,
                geo_report_path=self.geo_file, as_of=AT,
                extract_text=lambda path: PDF_TEXT,
            )
        self.assertFalse((self.folder / "extra-pdf-coverage-v1.json").exists())

    def test_changed_source_checksum_blocks_pdf_report(self):
        self.source.write_bytes(self.source.read_bytes() + b"{}")
        with self.assertRaisesRegex(CollectionError, "checksum"):
            audit_saved_extra_pdf(
                private_root=self.root, compare_dir=self.folder,
                geo_report_path=self.geo_file, as_of=AT,
                extract_text=lambda path: PDF_TEXT,
            )


if __name__ == "__main__":
    unittest.main()
