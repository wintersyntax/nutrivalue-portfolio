"""TASK-032: synthetic Extra six-catalogue equivalence and honest region gate."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nutrivalue.providers.tjek_extra_region import (
    DEALER, eligible_catalogs, resolve_offers, capture_and_resolve,
)
from nutrivalue.providers.tjek_weekly_collect import CollectionError
from nutrivalue.providers.tjek_offline import parse_snapshot

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-10-15T12:00:00+02:00"
FROM = "2026-10-12T00:00:00+02:00"
TILL = "2026-10-19T00:00:00+02:00"
STAMP = datetime.fromisoformat("2026-10-15T12:05:00+02:00")
IDS = ("9_IHxhJ1", "bN5uh-ck", "OlLLj9yO", "W8l7eqAB", "q2pYHF0J", "-_VgmnFu")


def catalog(cid, *, count=1, all_stores=False, end=TILL):
    return {"id": cid, "dealer_id": DEALER, "label": "",
            "run_from": FROM, "run_till": end, "publish": FROM,
            "offer_count": count, "all_stores": all_stores,
            "page_count": 26, "types": ["paged"]}


def offer(cid, number=0, name="SYNTHETIC EXTRA FOOD"):
    return {"id": f"{cid}-offer-{number}", "catalog_id": cid,
            "dealer_id": DEALER, "heading": name,
            "description": "SYNTHETIC TEST, 500 g",
            "pricing": {"price": 49.90, "pre_price": None, "currency": "NOK"},
            "quantity": {"unit": {"symbol": "g",
                                  "si": {"symbol": "kg", "factor": 0.001}},
                         "size": {"from": 500, "to": 500},
                         "pieces": {"from": 1, "to": 1}},
            "run_from": FROM, "run_till": TILL}


class Fake:
    def __init__(self):
        self.calls = []
        self.values = {cid: [offer(cid)] for cid in IDS}
        self.fail_at = None

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.fail_at == len(self.calls):
            raise CollectionError("Tjek HTTP 429; no retry")
        if endpoint != "offers":
            raise AssertionError("no catalog-list GET allowed")
        cid = params["catalog_id"]
        return self.values[cid][params["offset"]:params["offset"] + params["limit"]]


class ExtraRegionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "private"
        self.root.mkdir()
        self.metadata_dir = self.root / "2026-W42" / "diagnostic-20261015T235137Z"
        self.metadata_dir.mkdir(parents=True)
        self.metadata = self.metadata_dir / "80742m.catalogs.json"
        self.records = [catalog(cid) for cid in IDS]
        self.records.append(catalog("WprgprHV", count=0, all_stores=True,
                                    end="2026-11-01T22:59:59+01:00"))
        self.metadata.write_text(json.dumps(self.records))
        self.fake = Fake()

    def capture(self):
        return capture_and_resolve(
            metadata_path=self.metadata, private_root=self.root,
            as_of=AT, get_json=self.fake,
            captured_at=STAMP, run_id="20261015T100500Z")

    def folder(self):
        return self.root / "2026-W42" / "extra-compare-20261015T100500Z"

    def test_six_blank_label_weekly_catalogues_and_zero_offer_magazine(self):
        result = eligible_catalogs(self.records, AT)
        self.assertEqual({x["id"] for x in result}, set(IDS))
        self.assertEqual(len(result), 6)

    def test_no_network_plan(self):
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/resolve_tjek_extra_region.py"),
            "--metadata", str(self.metadata), "--as-of", AT,
        ], cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NO HTTP / NO FILES WRITTEN", result.stdout)
        self.assertIn("candidate Extra catalogues: 6", result.stdout)

    def test_identical_source_content_stays_region_unverified(self):
        report = self.capture()
        self.assertEqual(report["http_requests"], 6)
        self.assertEqual(report["shared_offers_in_memory"], 1)
        self.assertTrue(report["source_catalog_counts_match"])
        self.assertEqual(report["status"], "observed_content_equivalent_region_unproven")
        self.assertFalse(report["comparison"]["region_identity_verified"])
        self.assertIsNone(report["comparison"]["verified_tjek_catalog_for_ost"])
        self.assertEqual(report["comparison"]["common_offer_facts"], 1)
        self.assertEqual(len(self.fake.calls), 6)
        self.assertTrue(all(ep == "offers" for ep, _ in self.fake.calls))
        self.assertNotIn("SYNTHETIC EXTRA FOOD",
                         (self.folder() / "manifest.json").read_text())
        for cid in IDS:
            filename = self.folder() / f"{cid}.json"
            self.assertEqual(os.stat(filename).st_mode & 0o777, 0o600)
            snapshot = parse_snapshot(filename.read_bytes(), f"{cid}.json")
            self.assertEqual(snapshot.catalogs[0][4], None)  # blank label -> NULL
            self.assertEqual(snapshot.offers[0][-1], "source_only")

    def test_one_regional_difference_kept_out_of_common_offers(self):
        cid = IDS[0]
        self.fake.values[cid].append(offer(cid, 1, "OTHER SYNTHETIC ITEM"))
        self.records[0]["offer_count"] = 2
        self.metadata.write_text(json.dumps(self.records))
        report = self.capture()
        self.assertEqual(report["comparison"]["common_offer_facts"], 1)
        self.assertEqual(report["status"], "common_subsets_only_region_unproven")
        self.assertIsNone(report["comparison"]["verified_tjek_catalog_for_ost"])
        row = next(x for x in report["comparison"]["counts"] if x["catalog_id"] == cid)
        self.assertEqual(row["unique_to_this_candidate"], 1)

    def test_tjek_offer_id_image_and_page_changes_do_not_change_product_facts(self):
        by_id = {cid: [offer(cid)] for cid in IDS}
        by_id[IDS[0]][0]["catalog_page"] = 5
        by_id[IDS[1]][0]["images"] = {"view": "synthetic"}
        summary, common = resolve_offers([catalog(cid) for cid in IDS], by_id)
        self.assertTrue(summary["same_contents"])
        self.assertEqual(len(common), 1)

    def test_promotion_or_price_difference_blocks_exact_equivalence(self):
        by_id = {cid: [offer(cid)] for cid in IDS}
        by_id[IDS[0]][0]["description"] = "MEMBER ONLY"
        summary, common = resolve_offers([catalog(cid) for cid in IDS], by_id)
        self.assertFalse(summary["same_contents"])
        self.assertEqual(common, [])
        by_id = {cid: [offer(cid)] for cid in IDS}
        by_id[IDS[1]][0]["pricing"]["price"] = 69.90
        summary, common = resolve_offers([catalog(cid) for cid in IDS], by_id)
        self.assertFalse(summary["same_contents"])
        self.assertEqual(common, [])

    def test_duplicate_same_product_in_one_catalog_does_not_duplicate_common(self):
        by_id = {cid: [offer(cid)] for cid in IDS}
        by_id[IDS[0]].append(offer(IDS[0], 3))
        summary, common = resolve_offers([catalog(cid) for cid in IDS], by_id)
        self.assertTrue(summary["same_contents"])
        self.assertEqual(len(common), 1)

    def test_429_stops_after_two_gets_without_false_match(self):
        self.fake.fail_at = 2
        report = self.capture()
        self.assertEqual(report["http_requests"], 2)
        self.assertEqual(report["status"], "partial_or_unproven")
        self.assertEqual(report["shared_offers_in_memory"], 0)
        self.assertIn("429", report["halted_reason"])
        self.assertTrue((self.folder() / f"{sorted(IDS)[0]}.json").exists())

    def test_100_plus_1_paginated_offers_for_all_six(self):
        for cid in IDS:
            self.fake.values[cid] = [
                offer(cid, n, f"SYNTHETIC ITEM {n}") for n in range(101)
            ]
        self.records = [catalog(cid, count=101) for cid in IDS]
        self.metadata.write_text(json.dumps(self.records))
        report = self.capture()
        self.assertEqual(report["http_requests"], 12)
        self.assertEqual(report["shared_offers_in_memory"], 101)
        self.assertTrue(report["source_catalog_counts_match"])

    def test_declared_shortfalls_do_not_claim_complete(self):
        self.records = [catalog(cid, count=118) for cid in IDS]
        self.metadata.write_text(json.dumps(self.records))
        report = self.capture()
        self.assertEqual(report["attempts"][0]["shortfall"], 117)
        self.assertFalse(report["source_catalog_counts_match"])

    def test_foreign_dealer_or_invalid_source_fails_closed(self):
        self.records[0]["dealer_id"] = "faa0Ym"
        self.metadata.write_text(json.dumps(self.records))
        with self.assertRaisesRegex(CollectionError, "foreign"):
            self.capture()
        self.assertEqual(self.fake.calls, [])

    def test_weekly_repeat_requires_no_new_http(self):
        self.capture()
        with self.assertRaisesRegex(CollectionError, "already attempted"):
            self.capture()
        self.assertEqual(len(self.fake.calls), 6)


if __name__ == "__main__":
    unittest.main()
