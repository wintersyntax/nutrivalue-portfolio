"""TASK-032: deterministic four-dealer, metadata-only Tjek diagnostics.

No real Tjek requests; private fake fixtures contain no commercial data.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nutrivalue.providers.tjek_catalog_diagnostic import (
    MISSING_DEALERS, catalog_reasons, diagnose_missing_catalogs,
)
from nutrivalue.providers.tjek_weekly_collect import CollectionError

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-10-15T12:00:00+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-19T00:00:00+02:00"
STAMP = datetime.fromisoformat("2026-10-15T12:05:00+02:00")
RUN = "20261015T100500Z"


def cat(dealer, name="Uke 42", start=START, end=END, count=12, suffix="a"):
    return {
        "id": f"cat-{dealer}-{suffix}", "dealer_id": dealer,
        "label": name, "offer_count": count, "run_from": start,
        "run_till": end, "publish": START,
    }


class Fake:
    def __init__(self):
        self.calls = []
        self.mapping = {dealer: [cat(dealer)] for dealer in MISSING_DEALERS}
        self.fail_at = None

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.fail_at == len(self.calls):
            raise CollectionError("Tjek HTTP 429; no retry")
        if endpoint != "catalogs":
            raise AssertionError("offer requests are not permitted")
        return self.mapping[params["dealer_ids"]]


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "private"
        self.fake = Fake()

    def run_diag(self, **kwargs):
        return diagnose_missing_catalogs(
            as_of=AT, out_dir=self.path, get_json=self.fake,
            run_id=RUN, captured_at=STAMP, **kwargs)

    def folder(self):
        return self.path / "2026-W42" / f"diagnostic-{RUN}"

    def test_scope_is_exactly_four_unmatched_dealers(self):
        self.assertEqual(MISSING_DEALERS,
                         ("f5d5lm", "80742m", "b3e8Fm", "2686gD"))

    def test_four_metadata_gets_no_offers_and_private_immutable_files(self):
        report = self.run_diag()
        self.assertEqual(report["http_requests"], 4)
        self.assertEqual(report["max_requests"], 4)
        self.assertEqual(report["offers_fetched"], 0)
        self.assertIsNone(report["halted_reason"])
        self.assertEqual(len(self.fake.calls), 4)
        self.assertTrue(all(p == "catalogs" for p, _ in self.fake.calls))
        self.assertEqual({params["dealer_ids"] for _, params in self.fake.calls},
                         set(MISSING_DEALERS))
        self.assertTrue(all(params["limit"] == 100
                            for _, params in self.fake.calls))
        for dealer in MISSING_DEALERS:
            path = self.folder() / f"{dealer}.catalogs.json"
            self.assertEqual(json.loads(path.read_bytes()), self.fake.mapping[dealer])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        manifest = json.loads((self.folder() / "manifest.json").read_text())
        self.assertEqual(manifest["http_requests"], 4)
        self.assertNotIn("cat-f5d5lm", json.dumps(manifest))
        self.assertEqual(manifest["chains"][0]["weekly_filter_matches"], 1)
        self.assertEqual(manifest["chains"][0]["reasons"]["eligible_weekly"], 1)

    def test_classifies_active_nonweekly_labels_independently_of_availability(self):
        dealer = MISSING_DEALERS[0]
        values = [
            cat(dealer, name="Kundeavis", suffix="nonweek"),
            cat(dealer, name="Uke 42", count=0, suffix="zero"),
            cat(dealer, name="Uke 42", count=None, suffix="null"),
            cat(dealer, name="Uke 42", suffix="valid"),
            cat(dealer, name="Uke 41", end="2026-10-14T00:00:00+02:00",
                suffix="expired"),
            cat(dealer, name="Uke 43", start="2026-10-19T00:00:00+02:00",
                end="2026-10-26T00:00:00+02:00", suffix="future"),
        ]
        values.append({**cat(dealer, suffix="published"),
                       "publish": "2026-10-16T00:00:00+02:00"})
        row = catalog_reasons(values, dealer, AT)
        self.assertEqual(row["returned_catalogs"], 7)
        self.assertEqual(row["possible_active_other_labels"], 1)
        self.assertEqual(row["weekly_filter_matches"], 1)
        self.assertEqual(row["reasons"]["active_zero_offers"], 1)
        self.assertEqual(row["reasons"]["active_missing_offer_count"], 1)
        self.assertEqual(row["reasons"]["expired"], 1)
        self.assertEqual(row["reasons"]["future"], 1)
        self.assertEqual(row["reasons"]["published_later"], 1)

    def test_empty_list_does_not_claim_no_offers(self):
        dealer = MISSING_DEALERS[0]
        self.fake.mapping[dealer] = []
        report = self.run_diag()
        self.assertEqual(report["http_requests"], 4)
        self.assertEqual(report["chains"][0]["returned_catalogs"], 0)
        self.assertEqual(report["chains"][0]["weekly_filter_matches"], 0)
        self.assertEqual(report["chains"][0]["status"], "diagnosed")
        self.assertIn("does not prove", report["disclaimer"])

    def test_catalog_page_at_hundred_is_marked_truncated(self):
        dealer = MISSING_DEALERS[0]
        response = [cat(dealer, suffix=str(i)) for i in range(100)]
        summary = catalog_reasons(response, dealer, AT)
        self.assertTrue(summary["response_limit_reached"])
        self.assertEqual(summary["weekly_filter_matches"], 100)

    def test_malformed_cross_dealer_record_halting_no_more_network(self):
        d0 = MISSING_DEALERS[0]
        d1 = MISSING_DEALERS[1]
        self.fake.mapping[d0] = [cat(d1)]
        report = self.run_diag()
        self.assertEqual(report["http_requests"], 1)
        self.assertEqual(report["chains"][0]["status"], "error")
        self.assertEqual(report["chains"][1]["status"], "not_attempted")
        self.assertEqual(report["offers_fetched"], 0)
        self.assertFalse((self.folder() / f"{d0}.catalogs.json").exists())

    def test_http_429_stops_and_preserves_prior_metadata(self):
        self.fake.fail_at = 2
        report = self.run_diag()
        self.assertEqual(report["http_requests"], 2)
        self.assertEqual(report["chains"][0]["status"], "diagnosed")
        self.assertEqual(report["chains"][1]["status"], "error")
        self.assertEqual(report["chains"][2]["status"], "not_attempted")
        self.assertTrue((self.folder() / f"{MISSING_DEALERS[0]}.catalogs.json").is_file())
        self.assertIn("429", report["halted_reason"])

    def test_repeated_run_rejected_before_any_http_call(self):
        self.run_diag()
        with self.assertRaisesRegex(CollectionError, "already attempted"):
            self.run_diag()
        self.assertEqual(len(self.fake.calls), 4)

    def test_invalid_timestamp_and_run_id_fail_before_network(self):
        for at in ("2026-10-15T12:00:00", "wrong"):
            with self.assertRaises(CollectionError):
                diagnose_missing_catalogs(
                    as_of=at, out_dir=self.path, get_json=self.fake,
                    run_id=RUN, captured_at=STAMP)
        with self.assertRaisesRegex(CollectionError, "unsafe"):
            diagnose_missing_catalogs(as_of=AT, out_dir=self.path,
                                      get_json=self.fake, run_id="../unsafe",
                                      captured_at=STAMP)
        self.assertEqual(self.fake.calls, [])

    def test_missing_dealer_and_invalid_count_cannot_be_published(self):
        dealer = MISSING_DEALERS[0]
        for invalid in (True, -1, 6000, "12"):
            record = cat(dealer, count=invalid)
            with self.subTest(count=invalid), self.assertRaises(CollectionError):
                catalog_reasons([record], dealer, AT)
        with self.assertRaises(CollectionError):
            catalog_reasons([cat(dealer)], "faa0Ym", AT)

    def test_cli_plan_never_requests_network(self):
        res = subprocess.run(
            [sys.executable, str(ROOT / "scripts/diagnose_tjek_catalogs.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("NO HTTP / NO FILES", res.stdout)
        for item in ("Coop Prix", "Extra", "Joker", "Matkroken"):
            self.assertIn(item, res.stdout)
        self.assertIn("Maximum four GET", res.stdout)


if __name__ == "__main__":
    unittest.main()
