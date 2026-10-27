"""TASK-034A: synthetic schema survey and opt-in diagnostic integration."""
from __future__ import annotations

import hashlib
import http.client
from html import escape
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_tjek_app_cache import Response, body, line
from test_tjek_site_structured import site_row
from nutrivalue.providers.tjek_app_cache import AppCacheClient, AppCacheError

try:
    from nutrivalue.providers import tjek_product_diagnostic as diagnostic
except ImportError:
    diagnostic = None


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = {"publicId": "synthetic-1", "retailer": "KIWI", "product_class": "packaged_branded"}
# Entirely synthetic, not a claim about any real product or a valid check digit.
DIGITS = "0123456789012"


class ProductDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(diagnostic, "TASK-034A product diagnostic is not implemented")

    def test_nested_ean_gtin_and_barcode_arrays_are_counted_without_values(self):
        result = diagnostic.survey_products([
            {"package": {"ean": DIGITS, "gtin": {"gtin14": "00012345678901"}},
             "barcodes": ["123", "01234567"], "ean13": None}])
        self.assertEqual(result["product_objects_total"], 1)
        self.assertEqual(result["gtin_like_candidates"], 4)
        self.assertEqual(result["structurally_valid_gtin_candidates"], 3)
        self.assertEqual(result["identifier_field_counts"]["ean"], 1)
        self.assertEqual(result["identifier_field_counts"]["barcodes"], 1)
        rendered = json.dumps(result)
        self.assertNotIn(DIGITS, rendered)
        self.assertNotIn("00012345678901", rendered)
        self.assertIn("products[].package.ean", rendered)
        self.assertIn("null", rendered)

    def test_internal_ids_skus_and_product_codes_are_never_gtin_candidates(self):
        result = diagnostic.survey_products([{"id": DIGITS, "sku": DIGITS,
                                               "productCode": DIGITS, "vendorCode": DIGITS}])
        self.assertEqual(result["gtin_like_candidates"], 0)
        self.assertEqual(result["structurally_valid_gtin_candidates"], 0)
        self.assertEqual(result["identifier_field_counts"], {"productCode": 1})
        self.assertIn("products[].vendorCode", result["unexpected_identifier_fields"])

    def test_typed_identifier_objects_require_explicit_gtin_semantics(self):
        result = diagnostic.survey_products([{"identifiers": [
            {"type": "GTIN", "value": DIGITS},
            {"type": "SKU", "value": DIGITS},
            {"scheme": "ean13", "value": DIGITS},
            {"value": DIGITS}]}])
        self.assertEqual(result["gtin_like_candidates"], 2)
        self.assertEqual(result["structurally_valid_gtin_candidates"], 2)

    def test_words_containing_ean_and_untyped_numeric_codes_are_not_identifiers(self):
        result = diagnostic.survey_products([{"clean": DIGITS, "ocean": DIGITS,
                                               "cleanupCode": DIGITS, "supplierEAN13": DIGITS}])
        self.assertEqual(result["gtin_like_candidates"], 1)
        self.assertNotIn("products[].clean", result["unexpected_identifier_fields"])
        self.assertIn("products[].supplierEAN13", result["unexpected_identifier_fields"])

    def test_numeric_identifiers_do_not_regain_lost_leading_zeros(self):
        result = diagnostic.survey_products([{"ean": 12345678, "gtin": True,
                                               "upc": 12.5, "ean13": "１２３４５６７８９０１２３"}])
        self.assertEqual(result["gtin_like_candidates"], 1)
        self.assertEqual(result["structurally_valid_gtin_candidates"], 0)

    def test_unexpected_nested_gtin_field_is_discovered(self):
        result = diagnostic.survey_products([{"future": {"supplierGtin": DIGITS}}])
        self.assertEqual(result["gtin_like_candidates"], 1)
        self.assertIn("products[].future.supplierGtin", result["unexpected_identifier_fields"])

    def test_malformed_strings_are_not_repaired_or_checksummed_in_task_a(self):
        result = diagnostic.survey_products([{"ean": [" 01234567", "0123-4567", "00000000", "123"]}])
        self.assertEqual(result["gtin_like_candidates"], 2)
        self.assertEqual(result["structurally_valid_gtin_candidates"], 1)
        self.assertFalse(result["gs1_check_digit_validated"])

    def test_sensitive_subtrees_and_dynamic_field_names_do_not_leak(self):
        result = diagnostic.survey_products([{
            "accessToken": {"ean": DIGITS}, "private payload / not a field": "private-value",
            "metadata": {"gtin13": DIGITS}, "password": "private-password"}])
        rendered = json.dumps(result)
        self.assertEqual(result["gtin_like_candidates"], 1)
        for forbidden in ("accessToken", "password", "private-value", "private-password",
                          "private payload / not a field", DIGITS):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("<redacted>", rendered)
        self.assertIn("<dynamic>", rendered)

    def test_identifier_maps_redact_alphanumeric_dynamic_keys(self):
        result = diagnostic.survey_products([{"identifiers": {"ABCprivateIdentifier123": "ean"},
                                               "barcodes": {"MapPrivate123": {"value": DIGITS}}}])
        rendered = json.dumps(result)
        self.assertNotIn("ABCprivateIdentifier123", rendered)
        self.assertNotIn("MapPrivate123", rendered)
        self.assertIn("products[].identifiers.<dynamic>", rendered)

    def test_singular_identifier_maps_and_dynamic_descendants_remain_redacted(self):
        result = diagnostic.survey_products([{
            "barcode": {"ABCprivateIdentifier123": "EAN"},
            "identifiers": {"first": {"AnotherPrivateIdentifier123": "EAN"}},
            "gtin": {"NestedPrivate123": {"YetAnotherPrivate123": "EAN"}}}])
        rendered = json.dumps(result)
        for private_key in ("ABCprivateIdentifier123", "AnotherPrivateIdentifier123",
                            "NestedPrivate123", "YetAnotherPrivate123"):
            self.assertNotIn(private_key, rendered)
        self.assertIn("products[].identifiers.<dynamic>.<dynamic>", rendered)

    def test_explicit_non_gtin_or_conflicting_types_override_barcode_wrapper(self):
        result = diagnostic.survey_products([{"barcodes": [
            {"type": "SKU", "value": DIGITS}, {"type": "internal", "value": DIGITS},
            {"type": "GTIN", "scheme": "SKU", "value": DIGITS},
            {"type": "GTIN", "value": DIGITS}]}])
        self.assertEqual(result["gtin_like_candidates"], 1)
        self.assertEqual(result["structurally_valid_gtin_candidates"], 1)

    def test_known_normalized_barcode_variants_are_candidates(self):
        result = diagnostic.survey_products([{"barCode": DIGITS, "bar_codes": [DIGITS]}])
        self.assertEqual(result["gtin_like_candidates"], 2)
        self.assertEqual(result["structurally_valid_gtin_candidates"], 2)

    def test_explicit_gtin_wrappers_preserve_value_context_only(self):
        result = diagnostic.survey_products([{
            "barcode": {"value": DIGITS, "price": DIGITS, "id": DIGITS},
            "gtin": {"value": DIGITS},
            "identifiers": {"value": DIGITS}, "productCode": {"value": DIGITS}}])
        self.assertEqual(result["gtin_like_candidates"], 2)
        self.assertEqual(result["structurally_valid_gtin_candidates"], 2)

    def test_http_parser_failure_is_sanitized_in_the_end_to_end_report(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append(1)
            raise http.client.BadStatusLine("synthetic-private-response")

        client = AppCacheClient("synthetic-only", live=True, opener=opener)
        result = diagnostic.diagnose([SAMPLE], client=client, live=True)
        self.assertEqual(result["errors_by_type"], {"network": 1})
        self.assertEqual(result["status"], "partial_or_error")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("synthetic-private-response", json.dumps(result))

    def test_schema_work_budget_rejects_deep_or_oversized_graphs(self):
        deep = {}
        current = deep
        for _ in range(30):
            current["nested"] = {}
            current = current["nested"]
        for value in ([deep], [{"values": list(range(20001))}]):
            with self.subTest(kind=type(value).__name__), self.assertRaises(AppCacheError):
                diagnostic.survey_products(value)

    def test_duplicate_samples_are_deduplicated_and_conflicts_rejected(self):
        rows = diagnostic.sample_specs([SAMPLE, dict(SAMPLE)])
        self.assertEqual(len(rows), 1)
        with self.assertRaises(AppCacheError):
            diagnostic.sample_specs([SAMPLE, SAMPLE | {"retailer": "Extra"}])

    def test_manifest_is_bounded_and_rejects_credentials_or_unknown_group_labels(self):
        for samples in ([SAMPLE | {"api_key": "private"}],
                        [SAMPLE | {"retailer": "private-retailer"}],
                        [SAMPLE | {"product_class": "private-title"}],
                        [SAMPLE | {"publicId": "private / invalid"}],
                        [SAMPLE | {"publicId": f"synthetic-{i}"} for i in range(31)]):
            with self.subTest(count=len(samples)), self.assertRaises(AppCacheError):
                diagnostic.sample_specs(samples)

    def test_default_mode_is_network_free_even_with_client_available(self):
        calls = []
        client = AppCacheClient("synthetic-only", live=True, opener=lambda *a, **k: calls.append(1))
        result = diagnostic.diagnose([SAMPLE], client=client)
        self.assertEqual((result["status"], result["offers_tested"], result["app_cache_requests"]),
                         ("offline_plan", 0, 0))
        self.assertEqual(calls, [])

    def test_real_client_to_diagnostic_seam_preserves_empty_success_and_group_counts(self):
        samples = [SAMPLE, SAMPLE | {"publicId": "synthetic-2", "retailer": "Extra",
                                   "product_class": "fresh_generic"}]
        raw = body(line(oid="synthetic-2"), line(),
                   line("offerProducts", value=[]),
                   line("offerProducts", "synthetic-2", value=[{"nested": {"ean": DIGITS}}]))
        client = AppCacheClient("synthetic-only", live=True, opener=lambda *a, **k: Response(raw))
        result = diagnostic.diagnose(samples, client=client, live=True)
        expected = {"offers_tested": 2, "app_cache_requests": 1, "offer_success": 2,
                    "offerProducts_success": 2, "offerProducts_empty": 1,
                    "offerProducts_nonempty": 1, "product_objects_total": 1,
                    "gtin_like_candidates": 1, "structurally_valid_gtin_candidates": 1}
        for field, count in expected.items():
            self.assertEqual(result[field], count, field)
        self.assertEqual(result["results_by_retailer"]["Extra"]["offerProducts_nonempty"], 1)
        self.assertEqual(result["results_by_product_class"]["packaged_branded"]["offerProducts_empty"], 1)
        self.assertNotIn(DIGITS, json.dumps(result))
        self.assertFalse(result["rank_eligible"])
        self.assertEqual(result["source_family"], "tjek")
        self.assertEqual(result["source_endpoint"], "https://etilbudsavis.no/")
        from datetime import datetime, timedelta
        self.assertEqual(datetime.fromisoformat(result["observed_at"]).utcoffset(), timedelta(0))

    def test_all_empty_products_is_reported_explicitly(self):
        client = AppCacheClient("synthetic-only", live=True,
                               opener=lambda *a, **k: Response(body(line(), line("offerProducts", value=[]))))
        result = diagnostic.diagnose([SAMPLE], client=client, live=True)
        self.assertEqual(result["product_evidence"], "no_nonempty_offerProducts_observed")
        self.assertEqual(result["identifier_field_counts"], {})

    def test_empty_after_error_is_not_misreported_as_complete_negative_evidence(self):
        client = AppCacheClient("synthetic-only", live=True,
                               opener=lambda *a, **k: Response(body(line(status="error"),
                                                              line("offerProducts", status="error", value=[]))))
        result = diagnostic.diagnose([SAMPLE], client=client, live=True)
        self.assertEqual(result["status"], "partial_or_error")
        self.assertEqual(result["errors_by_type"]["api_status"], 2)
        self.assertEqual(result["product_evidence"], "no_successful_product_response")

    def test_transport_failure_stops_subsequent_batches_and_counts_attempted_offers(self):
        calls = []

        def opener(*a, **k):
            calls.append(1)
            raise TimeoutError("private-error")

        client = AppCacheClient("synthetic-only", live=True, opener=opener)
        result = diagnostic.diagnose([SAMPLE | {"publicId": f"synthetic-{i}"} for i in range(8)],
                                     client=client, live=True)
        self.assertEqual((result["offers_tested"], result["offers_not_tested"],
                          result["app_cache_requests"], len(calls)), (5, 3, 1, 1))
        self.assertEqual(result["errors_by_type"], {"timeout": 1})
        self.assertNotIn("private-error", json.dumps(result))

    def test_small_serial_batches_are_paced_and_do_not_exceed_thirty_offers(self):
        calls, sleeps = [], []

        def opener(request, timeout):
            import base64
            keys = [json.loads(base64.b64decode(k)) for k in json.loads(request.data)["data"]]
            calls.append(len(keys))
            lines = [line(kind, params["publicId"], value=[] if kind == "offerProducts" else None)
                     for kind, params in keys]
            return Response(body(*lines))

        client = AppCacheClient("synthetic-only", live=True, opener=opener)
        result = diagnostic.diagnose([SAMPLE | {"publicId": f"synthetic-{i}"} for i in range(30)],
                                     client=client, live=True, sleeper=sleeps.append)
        self.assertEqual((result["offers_tested"], result["app_cache_requests"]), (30, 6))
        self.assertEqual(calls, [10] * 6)
        self.assertEqual(sleeps, [1.0] * 5)

    def test_preexisting_client_requests_are_not_added_to_current_run(self):
        client = AppCacheClient("synthetic-only", live=True,
                               opener=lambda *a, **k: Response(body(line(), line("offerProducts", value=[]))))
        client.fetch_offers(["synthetic-1"])
        result = diagnostic.diagnose([SAMPLE], client=client, live=True)
        self.assertEqual(result["app_cache_requests"], 1)

    def test_missing_credentials_skip_live_without_touching_network(self):
        with patch.dict(os.environ, {}, clear=True):
            result = diagnostic.diagnose([SAMPLE], live=True)
        self.assertEqual(result["status"], "skipped_missing_credentials")
        self.assertEqual(result["app_cache_requests"], 0)

    def test_saved_html_id_discovery_reuses_task033_and_requires_sha(self):
        raw = ('<app-data data-status="success">' + escape(json.dumps({"data": [
            site_row(businessPublicId="257bxm"), site_row(publicId="second", businessPublicId="80742m")
        ]})) + '</app-data>').encode()
        with TemporaryDirectory() as temp:
            private = Path(temp)
            folder = private / "api-view-compare"
            folder.mkdir()
            path = folder / "etilbudsavis-husholdning-20261016T150436984303Z.html"
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            rows = diagnostic.html_samples(path, expected_sha256=digest, private_root=private)
            self.assertEqual([r["retailer"] for r in rows], ["KIWI", "Extra"])
            self.assertEqual(rows[0]["product_class"], "unknown")
            with self.assertRaises(AppCacheError):
                diagnostic.html_samples(path, expected_sha256="0" * 64, private_root=private)
            link = folder / "linked.html"
            link.symlink_to(path)
            with self.assertRaises(AppCacheError):
                diagnostic.html_samples(link, expected_sha256=digest, private_root=private)

    def test_cli_runs_directly_without_pythonpath_and_defaults_to_zero_requests(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        run = subprocess.run([sys.executable, str(ROOT / "scripts/diagnose_tjek_product_identifiers.py")],
                             capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], "offline_plan")
        self.assertEqual(result["offers_requested"], 1)
        self.assertEqual(result["app_cache_requests"], 0)
        self.assertNotIn("URg401m0FUzablaD9avIh", run.stdout)

    def test_private_sample_file_rejects_external_path_and_symlink(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            root.mkdir()
            outside = Path(temp) / "outside.json"
            outside.write_text(json.dumps([SAMPLE]))
            with self.assertRaises(AppCacheError):
                diagnostic.read_sample_file(outside, private_root=root)
            link = root / "link.json"
            link.symlink_to(outside)
            with self.assertRaises(AppCacheError):
                diagnostic.read_sample_file(link, private_root=root)
            path = root / "samples.json"
            path.write_text(json.dumps([SAMPLE]))
            self.assertEqual(diagnostic.read_sample_file(path, private_root=root), [SAMPLE])
