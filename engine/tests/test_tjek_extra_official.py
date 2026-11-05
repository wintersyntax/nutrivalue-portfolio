"""TASK-032: synthetic official Coop Øst PDF witness; no actual HTTP/PDF IO."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nutrivalue.providers.tjek_extra_official import (
    _hit, _norm, fetch_official_pdf, match_official_text,
    match_saved_private, official_pdf_url, validate_official_text,
)
from nutrivalue.providers.tjek_weekly_collect import CollectionError

AT = "2026-10-15T12:00:00+02:00"
FROM = "2026-10-12T00:00:00+02:00"
TILL = "2026-10-19T00:00:00+02:00"
CIDS = ("fake-regional-A", "fake-regional-B", "fake-regional-C")
GOOD_TEXT = "Extra\nØST\nPrisene gjelder uke 42 - 2026\n"


def offer(cid, suffix, *, price=29.90, heading=None):
    return {"id": f"{cid}-{suffix}", "dealer_id": "80742m",
            "catalog_id": cid, "heading": heading or f"TEST MATVARER {suffix}",
            "description": "SYNTHETIC TEST FOOD",
            "pricing": {"price": price, "pre_price": None, "currency": "NOK"},
            "quantity": {"unit": {"symbol": "g", "si": {"symbol": "kg", "factor": 0.001}},
                         "size": {"from": 500, "to": 500}, "pieces": None},
            "run_from": FROM, "run_till": TILL}


class CoopPdfTests(unittest.TestCase):
    def test_official_weekly_pdf_uses_verified_pattern_not_constant_catalog_id(self):
        self.assertEqual(official_pdf_url(AT),
                         "https://kundeavis.coop.no/aviser/extra/ost/ost-42.pdf")

    def test_region_and_week_must_both_appear(self):
        validate_official_text(GOOD_TEXT + " TEST FOOD", AT)
        for bad in ("Extra sør-vest uke 42 - 2026",
                    "Extra ØST Prisene gjelder uke 41 - 2026",
                    "Extra ØST Prisene gjelder uke 42 - 2025", ""):
            with self.subTest(text=bad), self.assertRaises(CollectionError):
                validate_official_text(bad, AT)

    def test_week_year_regex_accepts_printed_punctuation_and_layout(self):
        for good in (
            "EXTRA\nØST\nTilbudene gjelder uke 42 – 2026",
            "EXTRA\nØST\nTilbudene gjelder uke 42 - 2026",
            "EXTRA\nØST\n2026: Uke 42",
        ):
            with self.subTest(good=good):
                validate_official_text(good, AT)
        for wrong in (
            "EXTRA\nØST\nTilbud uke 41 – 2026",
            "EXTRA\nØST\nTilbud uke 42 – 2025",
            "EXTRA\nSØR\nTilbud uke 42 – 2026",
        ):
            with self.subTest(wrong=wrong), self.assertRaises(CollectionError):
                validate_official_text(wrong, AT)

    def test_identical_tjek_candidates_cannot_be_region_verified_from_pdf(self):
        rows = {cid: [offer(cid, 1)] for cid in CIDS}
        report = match_official_text(snapshots=rows,
                                     official_text=GOOD_TEXT +
                                     "TEST MATVARER 1 - 29 90",
                                     as_of=AT)
        self.assertEqual(report["status"], "region_unresolved")
        self.assertIsNone(report["candidate_for_ost"])
        self.assertEqual(report["shared_source_facts"], 1)
        self.assertFalse(report["verified_store_shelf_price"])

    def test_unique_three_discriminating_product_price_matches_select_candidate(self):
        rows = {cid: [offer(cid, "common", heading="COMMON TEST ITEM")]
                for cid in CIDS}
        for n in range(4):
            rows[CIDS[0]].append(offer(CIDS[0], n, heading=f"REGION EAST PRODUCT {n}"))
        for n in range(3):
            rows[CIDS[1]].append(offer(CIDS[1], n, heading=f"OTHER REGION PRODUCT {n}",
                                       price=39.90))
        text = GOOD_TEXT + "\n\f".join(
            f"REGION EAST PRODUCT {n} 29 90" for n in range(4))
        report = match_official_text(snapshots=rows, official_text=text, as_of=AT)
        self.assertEqual(report["status"], "ost_content_correlated")
        self.assertEqual(report["candidate_for_ost"], CIDS[0])
        scores = {e["catalog_id"]: e["official_pdf_matches"] for e in report["scores"]}
        self.assertEqual(scores[CIDS[0]], 4)
        self.assertEqual(scores[CIDS[1]], 0)
        self.assertFalse(report["verified_store_shelf_price"])

    def test_one_unique_match_is_not_enough_to_choose_east(self):
        rows = {cid: [offer(cid, 1)] for cid in CIDS}
        rows[CIDS[0]].append(offer(CIDS[0], "unique",
                                   heading="EAST ONLY TEST FOOD"))
        report = match_official_text(
            snapshots=rows, official_text=GOOD_TEXT +
            "EAST ONLY TEST FOOD 29 90", as_of=AT)
        self.assertEqual(report["status"], "region_unresolved")
        self.assertIsNone(report["candidate_for_ost"])

    def test_price_changes_on_same_product_prevent_mismatched_witness(self):
        o = offer(CIDS[0], 1, heading="ONE SPECIAL FOOD", price=19.90)
        self.assertFalse(_hit(o, [_norm("ONE SPECIAL FOOD 29 90")]))
        self.assertTrue(_hit(o, [_norm("ONE SPECIAL FOOD 19 90")]))

    def test_price_on_different_page_or_far_text_is_not_supportive(self):
        o = offer(CIDS[0], 1, heading="ONE SPECIAL FOOD", price=19.90)
        pages = [_norm("ONE SPECIAL FOOD"), _norm("OTHER PRODUCTS 19 90")]
        self.assertFalse(_hit(o, pages))
        self.assertFalse(_hit(o, [_norm("ONE SPECIAL FOOD " + "filler " * 100 + "19 90")]))

    def test_prohibited_official_pdf_host_is_rejected_before_http(self):
        with self.assertRaisesRegex(CollectionError, "unapproved"):
            fetch_official_pdf("https://example.com/aviser/extra/ost/ost-42.pdf",
                               opener=lambda *args, **kw: self.fail("network"))
        with self.assertRaisesRegex(CollectionError, "unapproved"):
            fetch_official_pdf("https://kundeavis.coop.no/aviser/extra/nord/nord-42.pdf",
                               opener=lambda *args, **kw: self.fail("network"))

    def test_pdf_body_bounded_and_header_checked(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return official_pdf_url(AT)
            def read(self, n): return b"<html>not a PDF</html>"
        with self.assertRaisesRegex(CollectionError, "invalid or oversized"):
            fetch_official_pdf(official_pdf_url(AT),
                               opener=lambda *args, **kw: Response())

    def test_private_official_match_writes_one_immutable_report_without_tjek_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "2026-W42" / "extra-compare-20261015T100500Z"
            folder.mkdir(parents=True)
            attempts = []
            for cid in CIDS:
                rows = [offer(cid, "common", heading="COMMON TEST FOOD")]
                if cid == CIDS[0]:
                    rows += [offer(cid, n, heading=f"REGION EAST PRODUCT {n}") for n in range(4)]
                envelope = {
                    "fetched_at": AT,
                    "entries": [{"catalog": {
                        "id": cid, "dealer_id": "80742m", "label": "",
                        "run_from": FROM, "run_till": TILL,
                        "offer_count": len(rows),
                    }, "offers": rows}],
                }
                data = json.dumps(envelope).encode("utf-8")
                (folder / f"{cid}.json").write_bytes(data)
                attempts.append({
                    "catalog_id": cid, "status": "source_saved",
                    "sha256": hashlib.sha256(data).hexdigest(),
                })
            (folder / "manifest.json").write_text(json.dumps({
                "halted_reason": None, "attempts": attempts,
            }))
            pdftxt = GOOD_TEXT + "\f".join(
                f"REGION EAST PRODUCT {n} 29 90" for n in range(4))
            calls = []
            def fetch(url):
                calls.append(url)
                return b"%PDF-synthetic-private"
            def extract(path):
                # macOS aliases /var to /private/var in resolved paths.
                self.assertEqual(path.resolve(), (folder / "coop-ost.pdf").resolve())
                return pdftxt
            result = match_saved_private(
                compare_dir=folder, private_root=root, as_of=AT,
                fetch_pdf=fetch, extract_text=extract)
            self.assertEqual(result["status"], "ost_content_correlated")
            self.assertEqual(result["candidate_for_ost"], CIDS[0])
            self.assertEqual(result["tjek_http_requests"], 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(os.stat(folder / "coop-ost.pdf").st_mode & 0o777, 0o600)
            self.assertNotIn("REGION EAST PRODUCT",
                             (folder / "official-ost-match.json").read_text())
            with self.assertRaisesRegex(CollectionError, "already attempted"):
                match_saved_private(compare_dir=folder, private_root=root,
                                    as_of=AT, fetch_pdf=fetch,
                                    extract_text=extract)
            self.assertEqual(len(calls), 1)

    def test_missing_pdf_extractor_keeps_region_unresolved_without_false_mapping(self):
        with patch("shutil.which", return_value=None):
            from nutrivalue.providers.tjek_extra_official import pdf_text_local
            with self.assertRaisesRegex(CollectionError, "pdftotext unavailable"):
                pdf_text_local(Path("/tmp/fake.pdf"))


if __name__ == "__main__":
    unittest.main()
