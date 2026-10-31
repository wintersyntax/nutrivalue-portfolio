"""TASK-033: one-request bounded eTilbudsavis host probe, synthetic HTTP only."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

from scripts.probe_tjek_etilbudsavis_host import (
    BYTE_LIMIT, HOST, PAGE_LIMIT, ProbeError, _one_response,
    probe_once, main,
)


def offer(oid="sample1"):
    return {
        "id": oid,
        "catalog_id": "W8l7eqAB",
        "dealer_id": "80742m",
        "heading": "Synthetic Extra cheese",
        "pricing": {"currency": "NOK", "price": 19.9},
        "run_from": "2026-10-12T00:00:00+02:00",
        "run_till": "2026-10-18T23:59:59+02:00",
    }


def original():
    return {
        "fetched_at": "2026-10-16T12:00:00+02:00",
        "entries": [{
            "catalog": {"id": "W8l7eqAB", "dealer_id": "80742m"},
            "offers": [offer()]
        }],
    }


class FakeResponse:
    def __init__(self, body, *, status=200, content_type="application/json"):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, cap):
        return self.body[:cap]


class EtilbudsavisProbeTests(unittest.TestCase):
    def test_only_one_get_approved_host_25_limit_zero_offset(self):
        rows = [offer() | {"membershipPrice": 15.9,
                           "pieceCountGet": 3, "pieceCountGetFor": 2}]
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, request.get_method(), timeout,
                          request.get_header("Accept")))
            return FakeResponse(json.dumps(rows).encode("utf-8"))

        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixed = datetime(2026, 10, 16, 13, 47, tzinfo=timezone.utc)
            result = probe_once(
                original=original(), catalog_id="W8l7eqAB",
                dealer_id="80742m", private_root=root,
                opener=opener, now=fixed)
            self.assertEqual(len(calls), 1)
            url, method, timeout, accept = calls[0]
            parts = urllib.parse.urlsplit(url)
            params = urllib.parse.parse_qs(parts.query)
            self.assertEqual(parts.hostname, HOST)
            self.assertEqual(parts.scheme, "https")
            self.assertEqual(parts.path, "/v2/offers")
            self.assertEqual(params, {
                "catalog_id": ["W8l7eqAB"],
                "limit": [str(PAGE_LIMIT)],
                "offset": ["0"],
            })
            self.assertEqual((method, timeout, accept),
                             ("GET", 12, "application/json"))
            self.assertEqual(result["http_requests"], 1)
            self.assertEqual(result["status"], "comparison_shape_compatible")
            self.assertEqual(result["comparison"]["id_overlap"], 1)
            self.assertEqual(result["comparison"]["exact_identity_compatible"], 1)
            self.assertEqual(result["optional_field_counts"]
                             ["field_nonnull:membershipPrice"], 1)
            self.assertEqual(result["optional_field_counts"]
                             ["field_nonnull:pieceCountGetFor"], 1)
            saved = root / "api-view-compare"
            snapshots = list(saved.glob("*.json"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(snapshots[0].read_text()), rows)
            self.assertFalse(result["rank_eligible"])
            self.assertTrue(result["source_only"])

    def test_unknown_second_host_shape_is_saved_and_not_falsely_matched(self):
        sample = [offer()]
        del sample[0]["catalog_id"]
        called = []

        def opener(request, timeout):
            called.append(1)
            return FakeResponse(json.dumps(sample).encode("utf-8"))

        with TemporaryDirectory() as temp:
            report = probe_once(
                original=original(), catalog_id="W8l7eqAB",
                dealer_id="80742m", private_root=Path(temp),
                opener=opener)
            self.assertEqual(len(called), 1)
            self.assertEqual(report["status"],
                             "second_host_fields_not_original_v2_shape")
            self.assertEqual(report["comparison"], {})
            self.assertEqual(len(list(
                (Path(temp) / "api-view-compare").glob("*.json"))), 1)

    def test_http_403_or_429_stops_once_without_saved_file(self):
        for code in (401, 403, 429):
            count = []

            def opener(request, timeout):
                count.append(1)
                raise urllib.error.HTTPError(
                    request.full_url, code, "access refused", {}, None)

            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ProbeError, f"HTTP {code}"):
                    probe_once(original=original(), catalog_id="W8l7eqAB",
                               dealer_id="80742m", private_root=Path(temp),
                               opener=opener)
                self.assertEqual(len(count), 1)
                self.assertFalse(
                    (Path(temp) / "api-view-compare").exists())

    def test_nonjson_response_refused(self):
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ProbeError, "non-JSON MIME"):
                probe_once(
                    original=original(), catalog_id="W8l7eqAB",
                    dealer_id="80742m", private_root=Path(temp),
                    opener=lambda request, timeout: FakeResponse(
                        b"<html>denied</html>", content_type="text/html"))

    def test_bounded_body_does_not_save_large_response(self):
        too_large = b"[" + b" " * BYTE_LIMIT + b"]"
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ProbeError, "oversized"):
                probe_once(
                    original=original(), catalog_id="W8l7eqAB",
                    dealer_id="80742m", private_root=Path(temp),
                    opener=lambda request, timeout: FakeResponse(too_large))
            self.assertFalse((Path(temp) / "api-view-compare").exists())

    def test_wrong_catalog_or_dealer_refused(self):
        for patch in ({"catalog_id": "wrong"},
                      {"dealer_id": "wrong"}):
            bad = [offer() | patch]
            with TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ProbeError, "foreign"):
                    probe_once(
                        original=original(), catalog_id="W8l7eqAB",
                        dealer_id="80742m", private_root=Path(temp),
                        opener=lambda request, timeout: FakeResponse(
                            json.dumps(bad).encode("utf-8")))

    def test_full_first_page_does_not_prove_complete_catalog(self):
        rows = [offer("synth-%03d" % i) for i in range(PAGE_LIMIT)]
        with TemporaryDirectory() as temp:
            r = probe_once(
                original=original(), catalog_id="W8l7eqAB",
                dealer_id="80742m", private_root=Path(temp),
                opener=lambda request, timeout: FakeResponse(
                    json.dumps(rows).encode()))
            self.assertTrue(r["page_filled_cap_coverage_unknown"])
            self.assertEqual(r["offers_sampled"], PAGE_LIMIT)
            self.assertEqual(r["comparison"]["id_overlap"], 0)

    def test_cli_requires_explicit_live_flag_before_file_access(self):
        with self.assertRaises(SystemExit) as failure:
            main(["--original", "/no/such/file", "--catalog-id", "W8l7eqAB",
                  "--dealer-id", "80742m"])
        self.assertEqual(failure.exception.code, 2)

    def test_reject_invalid_original_before_network(self):
        count = []

        def opener(request, timeout):
            count.append(1)
            raise AssertionError("must not make request")

        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                probe_once(
                    original=original(), catalog_id="OTHER",
                    dealer_id="80742m", private_root=Path(temp),
                    opener=opener)
        self.assertEqual(count, [])

    def test_empty_second_host_page_remains_no_coverage_claim(self):
        with TemporaryDirectory() as temp:
            r = probe_once(original=original(), catalog_id="W8l7eqAB",
                           dealer_id="80742m", private_root=Path(temp),
                           opener=lambda request, timeout: FakeResponse(b"[]"))
            self.assertEqual(r["offers_sampled"], 0)
            self.assertEqual(r["comparison"]["id_overlap"], 0)
            self.assertEqual(r["comparison"]["original_unseen_in_comparison"], 1)


if __name__ == "__main__":
    unittest.main()
