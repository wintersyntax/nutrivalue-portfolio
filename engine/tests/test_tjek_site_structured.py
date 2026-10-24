"""TASK-033: synthetic same-Tjek site JSON extraction and bounded page GET."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import urllib.error
from unittest.mock import patch

from nutrivalue.providers.tjek_site_structured import (
    SiteShapeError, extract_structured_offers, inspect_site_offers,
)
from scripts.probe_etilbudsavis_structured_page import (
    MAX_HTML_BYTES, URL, _fetch_once, page_probe,
    PageProbeError, NoRedirect,
)


def api_row():
    return {
        "id": "public-offer-123", "catalog_id": "W8l7eqAB",
        "dealer_id": "80742m", "heading": "Demo Protein",
        "description": "", "pricing": {"price": 29.9, "currency": "NOK"},
        "run_from": "2026-10-11T22:00:00Z",
        "run_till": "2026-10-18T21:59:59Z"
    }


def original():
    return {"entries": [{"catalog": {"id": "W8l7eqAB", "dealer_id": "80742m"},
                         "offers": [api_row()]}]}


def site_row(**kw):
    return {
        "publicId": "public-offer-123", "name": "Demo Protein",
        "businessPublicId": "80742m",
        "publicationPublicId": "W8l7eqAB",
        "currencyCode": "NOK", "price": 29.9,
        "membershipPrice": 24.9,
        "pieceCountGet": 3, "pieceCountGetFor": 2,
        "validFrom": "2026-10-11T22:00:00+0000",
        "validUntil": "2026-10-18T21:59:59+0000",
    } | kw


def site_html(rows):
    return ("<html><script>window.hydration=" +
            json.dumps({"data": rows}) +
            ";</script></html>").encode("utf-8")


class FakeHTML:
    status = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, maximum):
        return self.data[:maximum]


class SiteStructuredTests(unittest.TestCase):
    def test_literal_json_script_extracted_without_other_fields(self):
        rows = [site_row()]
        self.assertEqual(extract_structured_offers(site_html(rows)), rows)

    def test_json_whitespace_and_compact_serializations(self):
        rows = [site_row()]
        for encoded in (
            json.dumps({"data": rows}),
            json.dumps({"data": rows}, separators=(",", ":")),
            '{\n   "data" \t : \n  ' + json.dumps(rows) + '\n}',
        ):
            html = ("<script>window.__structured=" + encoded +
                    ";</script>").encode("utf-8")
            with self.subTest(json_prefix=encoded[:24]):
                self.assertEqual(extract_structured_offers(html), rows)

    def test_whitespace_before_key_in_script_json(self):
        rows = [site_row()]
        html = ('<script>const offers={ \n "data" : ' +
                json.dumps(rows) + '};</script>').encode("utf-8")
        self.assertEqual(extract_structured_offers(html), rows)

    def test_identity_and_price_member_get_pay_counts(self):
        report = inspect_site_offers(
            original(), [site_row()],
            catalog_id="W8l7eqAB", dealer_id="80742m")
        counts = report["counters"]
        self.assertEqual(counts["matched_exact_price_review_only"], 1)
        self.assertEqual(counts["matched_possible_member_field_review_only"], 1)
        self.assertEqual(counts["matched_get_n_pay_m_review_only"], 1)
        self.assertEqual(counts["selected_nonnull:membershipPrice"], 1)
        self.assertFalse(report["rank_eligible"])

    def test_absent_original_price_requires_review_never_tier(self):
        row = site_row(price=None, membershipPrice=24.9)
        report = inspect_site_offers(original(), [row],
            catalog_id="W8l7eqAB", dealer_id="80742m")
        counts = report["counters"]
        self.assertEqual(counts["matched_price_missing_one_side_review_only"], 1)
        self.assertNotIn("matched_possible_member_field_review_only", counts)

    def test_other_catalog_and_store_ignored(self):
        report = inspect_site_offers(original(),
              [site_row(businessPublicId="elsewhere"),
               site_row(publicationPublicId="different")],
              catalog_id="W8l7eqAB", dealer_id="80742m")
        self.assertEqual(report["counters"]["site_records_unfiltered"], 2)
        self.assertNotIn("matched_exact_price_review_only", report["counters"])

    def test_same_id_but_wrong_heading_or_date_is_not_verified(self):
        report = inspect_site_offers(original(),
              [site_row(name="Different product")],
              catalog_id="W8l7eqAB", dealer_id="80742m")
        self.assertEqual(report["counters"]["same_id_heading_conflict"], 1)
        other = inspect_site_offers(original(), [
            site_row(validFrom="2026-11-01T22:00:00Z")],
            catalog_id="W8l7eqAB", dealer_id="80742m")
        self.assertEqual(other["counters"]["same_id_dates_conflict"], 1)

    def test_site_id_not_in_original_is_review_only(self):
        report = inspect_site_offers(original(),
             [site_row(publicId="another-id")],
             catalog_id="W8l7eqAB", dealer_id="80742m")
        self.assertEqual(report["counters"]["site_id_not_in_original"], 1)

    def test_same_id_site_price_conflict_not_accepted(self):
        report = inspect_site_offers(original(), [
            site_row(price=1.0)], catalog_id="W8l7eqAB", dealer_id="80742m")
        self.assertEqual(report["counters"]["matched_price_conflict_review_only"], 1)
        self.assertNotIn("matched_possible_member_field_review_only",
                         report["counters"])

    def test_unrecognized_escaped_next_stream_fails_closed(self):
        with self.assertRaises(SiteShapeError):
            extract_structured_offers(
                b'<script>self.__next_f.push([1,"{\\"data\\":[1,2]}"])</script>')

    def test_raw_html_privately_written_once_and_only_aggregates_reported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            html = site_html([site_row()])
            result = page_probe(
                original=original(), private_root=root, html=html,
                now=datetime(2026, 10, 16, tzinfo=timezone.utc))
            self.assertEqual(result["status"], "site_sample_only")
            self.assertEqual(result["http_requests"], 0)
            self.assertEqual(result["counters"]["matched_exact_price_review_only"], 1)
            paths = list((root / "api-view-compare").glob("*.html"))
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].read_bytes(), html)
            self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)
            self.assertNotIn("Demo Protein", str(result))

    def test_one_get_exact_host_cap_and_no_redirect(self):
        html = site_html([site_row()])
        calls = []
        def fake_open(req, timeout):
            calls.append((req.full_url, timeout, req.get_method()))
            return FakeHTML(html)
        result = _fetch_once(opener=fake_open)
        self.assertEqual(result, html)
        self.assertEqual(calls, [(URL, 15, "GET")])
        with self.assertRaises(PageProbeError):
            NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://other/")

    def test_non_html_http_or_excess_bytes_stop_without_retry(self):
        with self.assertRaises(PageProbeError):
            _fetch_once(opener=lambda req, timeout:
                        FakeHTML(b"x" * (MAX_HTML_BYTES + 1)))
        def bad(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 429, "limit", {}, None)
        with self.assertRaisesRegex(PageProbeError, "429"):
            _fetch_once(opener=bad)

    def test_direct_cli_entrypoint_imports_without_pythonpath(self):
        # The owner invokes this as a file via scripts/ci-python, not as
        # a package module. A subprocess catches import-time failures
        # that would be hidden by PYTHONPATH=src:. during unittest.
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        run = subprocess.run(
            [sys.executable, str(root / "scripts/probe_etilbudsavis_structured_page.py"),
             "--help"],
            cwd=root, env=env, capture_output=True, text=True,
            timeout=10, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--live", run.stdout)

    def test_missing_json_is_captured_as_unrecognized_without_fake_data(self):
        with TemporaryDirectory() as tmp:
            result = page_probe(original=original(), private_root=Path(tmp),
                                html=b"<html><body>No JSON</body></html>")
            self.assertEqual(result["status"],
                             "saved_html_parser_shape_unrecognized")
            self.assertEqual(result["counters"], {})


if __name__ == "__main__":
    unittest.main()
