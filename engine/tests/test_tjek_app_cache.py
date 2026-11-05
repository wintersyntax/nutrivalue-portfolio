"""TASK-034A: synthetic app-cache protocol and transport boundaries only."""
from __future__ import annotations

import base64
import http.client
import json
import unittest
import urllib.error

try:
    from nutrivalue.providers import tjek_app_cache as cache
except ImportError:
    cache = None


def key(kind="offer", oid="synthetic-1", *, pretty=False):
    value = json.dumps([kind, {"publicId": oid}], ensure_ascii=False,
                       indent=2 if pretty else None)
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def line(kind="offer", oid="synthetic-1", *, value=None, status="success"):
    if value is None:
        value = {"publicId": oid, "currencyCode": "NOK", "name": "Synthetic ø milk"}
    return json.dumps({"key": key(kind, oid), "status": status, "value": value})


def body(*lines):
    return ("\n".join(lines) + "\n").encode("utf-8")


class Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, raw):
        self.raw = raw

    def read(self, cap):
        return self.raw[:cap]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class AppCacheTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cache, "TASK-034A app-cache client is not implemented")

    def test_utf8_base64_retains_non_ascii_json(self):
        result = cache.encode_cache_key(["offer", {"publicId": "synthetic-ø"}])
        self.assertEqual(base64.b64decode(result).decode("utf-8"),
                         '["offer",{"publicId":"synthetic-ø"}]')

    def test_batch_maps_reversed_responses_by_decoded_key_not_position(self):
        raw = body(line("offerProducts", "synthetic-2", value=[{"ean": "123"}]),
                   line(oid="synthetic-1"),
                   line(oid="synthetic-2"),
                   line("offerProducts", value=[]))
        wanted = [(kind, oid) for oid in ("synthetic-1", "synthetic-2")
                  for kind in ("offer", "offerProducts")]
        results = cache.parse_cache_response(raw, wanted)
        self.assertEqual(results["offerProducts", "synthetic-1"]["value"], [])
        self.assertEqual(results["offerProducts", "synthetic-2"]["value"], [{"ean": "123"}])
        self.assertEqual(results["offer", "synthetic-1"]["status"], "success")

    def test_equivalent_whitespace_in_response_key_maps_to_request(self):
        raw = body(json.dumps({"key": key(pretty=True), "status": "success",
                               "value": {"publicId": "synthetic-1", "currencyCode": "NOK"}}))
        self.assertEqual(cache.parse_cache_response(raw, [("offer", "synthetic-1")])
                         ["offer", "synthetic-1"]["status"], "success")

    def test_empty_products_success_is_not_error_or_missing(self):
        result = cache.parse_cache_response(body(line("offerProducts", value=[])),
                                             [("offerProducts", "synthetic-1")])
        self.assertEqual(result["offerProducts", "synthetic-1"],
                         {"status": "success", "value": [], "error": None})

    def test_per_key_failure_does_not_hide_other_success(self):
        result = cache.parse_cache_response(body(line(status="provider-private-error"),
                                line("offerProducts", value=[])),
                                [("offer", "synthetic-1"), ("offerProducts", "synthetic-1")])
        self.assertEqual(result["offer", "synthetic-1"]["error"], "api_status")
        self.assertNotIn("provider-private-error", json.dumps(list(result.values())))
        self.assertEqual(result["offerProducts", "synthetic-1"]["status"], "success")

    def test_missing_null_and_absent_response_are_separate_from_empty_array(self):
        wanted = [("offerProducts", "synthetic-1")]
        for payload, expected in (({"key": key("offerProducts"), "status": "success"}, "missing_value"),
                                  ({"key": key("offerProducts"), "status": "success", "value": None}, "invalid_value")):
            with self.subTest(expected=expected):
                result = cache.parse_cache_response(body(json.dumps(payload)), wanted)
                self.assertEqual(result["offerProducts", "synthetic-1"]["error"], expected)
        result = cache.parse_cache_response(body(line()),
                  [("offer", "synthetic-1"), *wanted])
        self.assertEqual(result["offerProducts", "synthetic-1"]["error"], "missing_response")

    def test_non_norwegian_or_foreign_offer_and_nonobject_products_fail_closed(self):
        for kind, value in (("offer", {"publicId": "different", "currencyCode": "NOK"}),
                            ("offer", {"publicId": "synthetic-1", "currencyCode": "DKK"}),
                            ("offer", {"publicId": "synthetic-1"}),
                            ("offerProducts", ["unexpected-string"]),
                            ("offerProducts", {})):
            with self.subTest(kind=kind, value=value):
                result = cache.parse_cache_response(body(line(kind, value=value)), [(kind, "synthetic-1")])
                self.assertEqual(result[kind, "synthetic-1"]["error"], "invalid_value")

    def test_unknown_product_fields_remain_available_for_schema_discovery(self):
        result = cache.parse_cache_response(body(line("offerProducts", value=[{"future": {"gtin": "123"}}])),
                                             [("offerProducts", "synthetic-1")])
        self.assertEqual(result["offerProducts", "synthetic-1"]["value"], [{"future": {"gtin": "123"}}])

    def test_malformed_ndjson_duplicate_keys_and_unsolicited_keys_reject_batch(self):
        valid = line()
        cases = (b"not JSON", body(valid, "broken"), body(valid, valid),
                 body(line(oid="unrequested")), b"[]", b"\xff",
                 body('{"key":"' + key() + '","status":"error","status":"success"}'),
                 body('{"key":"' + key() + '","status":"success","value":NaN}'))
        for raw in cases:
            with self.subTest(raw=raw[:20]), self.assertRaises(cache.AppCacheError):
                cache.parse_cache_response(raw, [("offer", "synthetic-1")])

    def test_client_requires_live_and_does_not_reveal_token_in_repr(self):
        calls = []
        client = cache.AppCacheClient("synthetic-secret", opener=lambda *a, **k: calls.append(a))
        with self.assertRaises(cache.AppCacheError) as error:
            client.fetch_offers(["synthetic-1"])
        self.assertEqual(error.exception.code, "live_not_enabled")
        self.assertEqual(calls, [])
        self.assertNotIn("synthetic-secret", repr(client))

    def test_request_is_bounded_post_with_both_keys_and_deduplicated_ids(self):
        captured = []

        def opener(request, timeout):
            captured.append((request, timeout))
            return Response(body(line(), line("offerProducts", value=[])))

        client = cache.AppCacheClient("synthetic-only", live=True, opener=opener)
        client.fetch_offers(["synthetic-1", "synthetic-1"])
        request, timeout = captured[0]
        self.assertEqual((request.full_url, request.method, timeout),
                         ("https://etilbudsavis.no/", "POST", 12))
        self.assertEqual(request.get_header("X-api-key"), "synthetic-only")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        decoded = [json.loads(base64.b64decode(k)) for k in json.loads(request.data)["data"]]
        self.assertEqual(decoded, [["offer", {"publicId": "synthetic-1"}],
                                   ["offerProducts", {"publicId": "synthetic-1"}]])
        self.assertEqual(client.requests, 1)

    def test_transport_errors_are_sanitized_and_never_retried(self):
        for failure, code in ((TimeoutError("synthetic-secret"), "timeout"),
                              (urllib.error.URLError("synthetic-secret"), "network"),
                              (urllib.error.HTTPError("private", 429, "synthetic-secret", {}, None), "http_429")):
            calls = []

            def opener(*args, **kwargs):
                calls.append(1)
                raise failure

            client = cache.AppCacheClient("synthetic-secret", live=True, opener=opener)
            with self.assertRaises(cache.AppCacheError) as error:
                client.fetch_offers(["synthetic-1"])
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("synthetic-secret", str(error.exception))
            self.assertEqual((len(calls), client.requests), (1, 1))

    def test_http_parser_exceptions_cannot_escape_with_response_text(self):
        for failure in (http.client.BadStatusLine("synthetic-private-response"),
                        http.client.IncompleteRead(b"synthetic-private-response", 100)):
            calls = []

            def opener(*args, **kwargs):
                calls.append(1)
                raise failure

            client = cache.AppCacheClient("synthetic-only", live=True, opener=opener)
            with self.assertRaises(cache.AppCacheError) as error:
                client.fetch_offers(["synthetic-1"])
            self.assertEqual(error.exception.code, "network")
            self.assertNotIn("synthetic-private-response", str(error.exception))
            self.assertEqual(len(calls), 1)

    def test_access_denials_stop_without_retry(self):
        for status in (401, 403):
            calls = []

            def opener(*args, **kwargs):
                calls.append(1)
                raise urllib.error.HTTPError("private", status, "private", {}, None)

            client = cache.AppCacheClient("synthetic-only", live=True, opener=opener)
            with self.assertRaises(cache.AppCacheError) as error:
                client.fetch_offers(["synthetic-1"])
            self.assertEqual(error.exception.code, f"http_{status}")
            self.assertEqual(len(calls), 1)

    def test_redirect_does_not_forward_authorization(self):
        handler = cache.NoRedirect()
        with self.assertRaises(cache.AppCacheError) as error:
            handler.redirect_request(None, None, 302, "private", {}, "https://other.invalid/")
        self.assertEqual(error.exception.code, "redirect")

    def test_oversized_response_and_batch_caps_prevent_more_work(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append(1)
            return Response(b"x" * (cache.MAX_RESPONSE_BYTES + 1))

        client = cache.AppCacheClient("synthetic-only", live=True, opener=opener)
        with self.assertRaises(cache.AppCacheError):
            client.fetch_offers(["synthetic-1"])
        with self.assertRaises(cache.AppCacheError):
            client.fetch_offers([f"synthetic-{i}" for i in range(6)])
        self.assertEqual(len(calls), 1)

    def test_six_request_cap_is_enforced_before_seventh_http_call(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append(1)
            return Response(body(line(), line("offerProducts", value=[])))

        client = cache.AppCacheClient("synthetic-only", live=True, opener=opener)
        for _ in range(6):
            client.fetch_offers(["synthetic-1"])
        with self.assertRaises(cache.AppCacheError) as error:
            client.fetch_offers(["synthetic-1"])
        self.assertEqual((error.exception.code, len(calls)), ("request_budget", 6))
