"""TASK-032: synthetic Tjek Extra geographic filtering, bounded and replayable."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nutrivalue.providers.tjek_geo_region import (
    EXTRA, POINTS, capture_geo, geo_evidence, replay_geo,
)
from nutrivalue.providers.tjek_weekly_collect import CollectionError

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-10-16T02:45:00+02:00"
START = "2026-10-12T00:00:00+02:00"
TILL = "2026-10-19T00:00:00+02:00"
STAMP = datetime.fromisoformat(AT)


def cat(cid, count=118, all_stores=False, dealer=EXTRA, end=TILL, published=START):
    return {
        "id": cid, "dealer_id": dealer, "offer_count": count,
        "run_from": START, "run_till": end, "publish": published,
        "all_stores": all_stores, "types": ["paged"], "label": "",
    }


def responses():
    global_mag = cat("mag-all-stores", count=0, all_stores=True,
                     end="2026-11-01T22:59:59+01:00")
    oslo = [cat("W8l7eqAB"), global_mag]
    bergen = [cat("9_IHxhJ1"), global_mag]
    return oslo, bergen


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.fail_at = None

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.fail_at == len(self.calls):
            raise CollectionError("Tjek HTTP 429; no retry")
        self.assert_coords(endpoint, params)
        oslo, bergen = responses()
        return oslo if params["r_lat"] == POINTS["oslo"][0] else bergen

    def assert_coords(self, endpoint, params):
        if endpoint != "catalogs" or params["dealer_ids"] != EXTRA:
            raise AssertionError("unexpected endpoint/dealer")
        if params.get("r_lat") not in (POINTS["oslo"][0], POINTS["bergen"][0]):
            raise AssertionError("not a public city point")


class GeographicExtraTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "private"
        self.root.mkdir()
        self.replay = self.root / "2026-W42" / "extra-api-investigation"
        self.replay.mkdir(parents=True)
        oslo, bergen = responses()
        for filename, data in (
            ("oslo_catalogs.json", oslo), ("bergen_catalogs.json", bergen),
            ("oslo_sdk_filter.json", oslo),
        ):
            (self.replay / filename).write_text(json.dumps(data))
        self.fake = FakeAPI()

    def test_owner_observed_oslo_and_bergen_are_distinct_with_global_magazine(self):
        oslo, bergen = responses()
        result = geo_evidence(oslo, bergen, as_of=AT, oslo_alt=oslo)
        self.assertEqual(result["status"], "geo_oslo_catalog_candidate")
        self.assertEqual(result["oslo_candidate_catalog_id"], "W8l7eqAB")
        self.assertEqual(result["bergen_control_catalog_id"], "9_IHxhJ1")
        self.assertEqual(result["shared_catalog_ids_in_responses"], 1)
        self.assertTrue(result["two_parameter_forms_agree"])
        self.assertFalse(result["coop_ost_identity_verified"])
        self.assertFalse(result["retail_shelf_price_verified"])

    def test_identical_or_ambiguous_geo_responses_never_select(self):
        oslo, bergen = responses()
        for left, right in (
            (oslo, oslo),
            ([*oslo, cat("extra-oslo-variant")], bergen),
            ([oslo[1]], bergen),
        ):
            with self.subTest(left=len(left), right=len(right)):
                r = geo_evidence(left, right, as_of=AT)
                self.assertEqual(r["status"], "geo_unresolved")
                self.assertIsNone(r["oslo_candidate_catalog_id"])

    def test_wrong_dealer_duplicate_and_provider_cap_fail_closed(self):
        oslo, bergen = responses()
        bads = [
            [cat("W8l7eqAB", dealer="f5d5lm")],
            [oslo[0], oslo[0]],
            [cat(f"cat-{n}") for n in range(100)],
            [cat("bad-count", count=True)],
            [cat("bad-all-stores", all_stores=None)],
            [cat("bad-date", end=START)],
        ]
        for bad in bads:
            with self.subTest(case=str(bad)[:35]), self.assertRaises(CollectionError):
                geo_evidence(bad, bergen, as_of=AT)

    def test_future_or_expired_catalogs_do_not_become_geo_candidate(self):
        oslo, bergen = responses()
        oslo.append(cat("future-pub", published="2026-10-22T00:00:00+02:00"))
        oslo.append(cat("expired", end="2026-10-15T00:00:00+02:00"))
        summary = geo_evidence(oslo, bergen, as_of=AT)
        self.assertEqual(summary["oslo_active_local_candidates"], 1)

    def test_sdk_singular_and_plural_dealer_filter_must_agree(self):
        oslo, bergen = responses()
        self.assertTrue(geo_evidence(oslo, bergen, as_of=AT,
                                     oslo_alt=oslo)["two_parameter_forms_agree"])
        with self.assertRaisesRegex(CollectionError, "disagree"):
            geo_evidence(oslo, bergen, as_of=AT,
                         oslo_alt=[cat("different")])

    def test_cli_default_plan_zero_files_or_network(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/resolve_tjek_geo_region.py"),
             "--as-of", AT], cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ZERO NETWORK / ZERO WRITES", result.stdout)

    def test_replay_private_json_zero_network_immutable_counts_only(self):
        old = (self.replay / "oslo_catalogs.json").read_bytes()
        report = replay_geo(private_root=self.root, input_dir=self.replay, as_of=AT)
        self.assertEqual(report["http_requests"], 0)
        self.assertEqual(report["oslo_candidate_catalog_id"], "W8l7eqAB")
        self.assertEqual((self.replay / "oslo_catalogs.json").read_bytes(), old)
        saved = self.replay / "extra-geo-resolution-v1.json"
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(CollectionError, "already attempted"):
            replay_geo(private_root=self.root, input_dir=self.replay, as_of=AT)

    def test_private_path_traversal_and_symlink_rejected(self):
        with self.assertRaisesRegex(CollectionError, "outside private root"):
            replay_geo(private_root=self.root,
                       input_dir=self.root.parent, as_of=AT)
        (self.replay / "oslo_catalogs.json").unlink()
        (self.replay / "oslo_catalogs.json").symlink_to(self.replay / "bergen_catalogs.json")
        with self.assertRaisesRegex(CollectionError, "symlinked"):
            replay_geo(private_root=self.root, input_dir=self.replay, as_of=AT)

    def test_live_uses_two_requests_only_and_keeps_raw_metadata(self):
        report = capture_geo(private_root=self.root, as_of=AT, get_json=self.fake,
                             captured_at=STAMP, run_id="20261016T004500Z")
        self.assertEqual(report["http_requests"], 2)
        self.assertEqual(report["resolution"]["oslo_candidate_catalog_id"], "W8l7eqAB")
        self.assertEqual(len(self.fake.calls), 2)
        self.assertTrue(all(ep == "catalogs" for ep, _ in self.fake.calls))
        folder = self.root / "2026-W42" / "extra-geo-20261016T004500Z"
        for name in ("oslo", "bergen"):
            self.assertEqual((folder / f"{name}.catalogs.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(CollectionError, "already attempted"):
            capture_geo(private_root=self.root, as_of=AT, get_json=self.fake)
        self.assertEqual(len(self.fake.calls), 2)

    def test_live_429_stops_second_geo_get_and_preserves_first(self):
        self.fake.fail_at = 2
        report = capture_geo(private_root=self.root, as_of=AT, get_json=self.fake,
                             captured_at=STAMP, run_id="20261016T004500Z")
        self.assertEqual(report["http_requests"], 2)
        self.assertIn("429", report["halted_reason"])
        self.assertIsNone(report["resolution"])
        folder = self.root / "2026-W42" / "extra-geo-20261016T004500Z"
        self.assertTrue((folder / "oslo.catalogs.json").exists())
        self.assertFalse((folder / "bergen.catalogs.json").exists())
        self.assertEqual(json.loads((folder / "manifest.json").read_text())["http_requests"], 2)


if __name__ == "__main__":
    unittest.main()
