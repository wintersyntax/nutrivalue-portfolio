"""TASK-033: synthetic entity-escaped app-data, never archived products."""
from __future__ import annotations

import hashlib
from html import escape
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from nutrivalue.providers.tjek_site_structured import (
    MAX_HTML_BYTES, MAX_SITE_RECORDS, SiteShapeError,
    extract_structured_offers, inspect_site_offers,
)
from scripts.inspect_etilbudsavis_html_shape import inspect_private_html
from test_tjek_site_structured import original, site_html, site_row


def app_data(payload, *, status="success"):
    return ('<app-data data-key="synthetic-cache" data-size="1" '
            'data-status="' + status + '">' +
            escape(json.dumps(payload), quote=True) + '</app-data>')


def page(*containers):
    return ('<html><body>' + ''.join(containers) + '</body></html>').encode()


class AppDataTests(unittest.TestCase):
    def test_entity_escaped_json_yields_complete_same_object_offer(self):
        rows = extract_structured_offers(page(app_data({"data": [site_row()]})))
        self.assertEqual(len(rows), 1)
        report = inspect_site_offers(original(), rows,
            catalog_id="W8l7eqAB", dealer_id="80742m")
        self.assertEqual(report["counters"]["matched_exact_price_review_only"], 1)
        self.assertFalse(report["rank_eligible"])

    def test_entities_are_decoded_once_preserving_literal_entity_in_name(self):
        rows = extract_structured_offers(page(app_data({
            "data": [site_row(name='Synthetic &quot;literal&quot; & <demo>')]})))
        self.assertEqual(rows[0]["name"], 'Synthetic &quot;literal&quot; & <demo>')

    def test_numeric_entities_and_unicode_json_escapes(self):
        html = app_data({"data": [site_row(name="Synthetic ø & demo")]})
        rows = extract_structured_offers(page(html.replace('&quot;', '&#34;')))
        self.assertEqual(rows[0]["name"], "Synthetic ø & demo")

    def test_disjoint_groups_aggregate_independently_of_container_order(self):
        one = app_data({"data": [site_row()]})
        two = app_data({"data": [site_row(publicId="synthetic-second")]})
        for blocks in ((one, two), (two, one)):
            rows = extract_structured_offers(page(*blocks))
            self.assertEqual({r["publicId"] for r in rows},
                             {"public-offer-123", "synthetic-second"})

    def test_incomplete_or_invalid_rows_are_discarded_without_borrowing(self):
        for field, value in (
            ("publicId", None), ("publicId", {}),
            ("publicationPublicId", None), ("businessPublicId", ""),
            ("name", None), ("name", "  "), ("price", None),
            ("price", True), ("price", -1), ("price", "NaN"),
            ("price", "1e999999999"),
            ("currencyCode", None), ("currencyCode", "USD"),
            ("validFrom", None), ("validFrom", "2026-10-11"),
            ("validFrom", "0001-01-01T00:00:00+01:00"),
            ("validUntil", "2026-10-01T00:00:00Z"),
        ):
            with self.subTest(field=field, kind=type(value).__name__):
                bad = site_row(publicId="incomplete") | {field: value}
                rows = extract_structured_offers(page(app_data({
                    "data": [bad, site_row()]})))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["publicId"], "public-offer-123")

    def test_split_attributes_do_not_create_offer(self):
        row = site_row()
        left = {k: v for k, v in row.items() if k != "price"}
        right = {"price": 29.9}
        with self.assertRaises(SiteShapeError):
            extract_structured_offers(page(app_data({"data": [left, right]})))

    def test_nested_related_business_is_not_used_to_fill_missing_fields(self):
        row = site_row(price=None, business={"price": 29.9})
        with self.assertRaises(SiteShapeError):
            extract_structured_offers(page(app_data({"data": [row]})))

    def test_unrelated_json_ld_dom_comments_and_nested_data_are_ignored(self):
        decoy = app_data({"data": [site_row()]})
        html = page('<!--' + decoy + '-->',
            '<div>' + escape(json.dumps({"data": [site_row()]})) + '</div>',
            '<script type="application/ld+json">' +
            json.dumps({"name": "Synthetic demo", "offers": {"price": 29.9}}) +
            '</script>', app_data({"unrelated": {"data": [site_row()]}}))
        with self.assertRaises(SiteShapeError):
            extract_structured_offers(html)

    def test_failed_missing_or_duplicate_container_status_is_not_accepted(self):
        for status in ("error", "pending", ""):
            with self.subTest(status=status), self.assertRaises(SiteShapeError):
                extract_structured_offers(page(app_data({"data": [site_row()]},
                                                        status=status)))
        for replacement in ('', 'data-status="error" data-status="success"'):
            html = app_data({"data": [site_row()]}).replace(
                'data-status="success"', replacement)
            with self.assertRaises(SiteShapeError):
                extract_structured_offers(page(html))

    def test_nested_markup_comment_and_unclosed_container_fail_closed(self):
        valid = app_data({"data": [site_row()]})
        for html in (valid.replace('&quot;price&quot;',
                                  '<span>&quot;price&quot;</span>'),
                     valid.replace('&quot;price&quot;', '<!--x-->&quot;price&quot;'),
                     valid.replace('</app-data>', ''),
                     '<app-data data-status="success">' + valid + '</app-data>'):
            with self.subTest(length=len(html)), self.assertRaises(SiteShapeError):
                extract_structured_offers(page(html))

    def test_no_execution_and_no_speculative_second_json_or_entity_decode(self):
        raw = json.dumps({"data": [site_row()]})
        for body in (escape(json.dumps(raw)), escape('JSON.parse(' + raw + ')'),
                     escape(escape(raw))):
            with self.assertRaises(SiteShapeError):
                extract_structured_offers(page(
                    '<app-data data-status="success">' + body + '</app-data>'))

    def test_exact_repeat_across_containers_deduplicated(self):
        html = app_data({"data": [site_row()]})
        self.assertEqual(len(extract_structured_offers(page(html, html))), 1)

    def test_conflicting_same_id_groups_fail_instead_of_choosing_longest(self):
        for other in (site_row(price=30), site_row(name="Different synthetic")):
            with self.assertRaises(SiteShapeError):
                extract_structured_offers(page(
                    app_data({"data": [site_row()]}),
                    app_data({"data": [other, site_row(publicId="second")]})))

    def test_incomplete_conflicting_same_id_is_not_silently_dropped(self):
        with self.assertRaises(SiteShapeError):
            extract_structured_offers(page(
                app_data({"data": [site_row()]}),
                app_data({"data": [site_row(price=None)]})))

    def test_script_comments_strings_and_regex_are_not_literal_offer_payloads(self):
        raw = json.dumps({"data": [site_row()]})
        for script in ('// ' + raw, '/* ' + raw + ' */',
                       "const label='" + raw + "';", 'const pattern=/' + raw + '/;',
                       'window.data=' + raw + '; unrelated();'):
            with self.subTest(length=len(script)), self.assertRaises(SiteShapeError):
                extract_structured_offers(page('<script>' + script + '</script>'))

    def test_duplicate_ids_inside_one_group_fail(self):
        with self.assertRaises(SiteShapeError):
            extract_structured_offers(page(app_data({
                "data": [site_row(), site_row()]})))

    def test_duplicate_json_keys_are_ambiguous_and_rejected(self):
        raw = json.dumps({"data": [site_row()]}).replace(
            '"price": 29.9', '"price": 1, "price": 29.9')
        with self.assertRaises(SiteShapeError):
            extract_structured_offers(page('<app-data data-status="success">' +
                                          escape(raw) + '</app-data>'))

    def test_total_record_budget_applies_across_groups_before_deduplication(self):
        group = app_data({"data": [site_row(publicId=f"synthetic-{i}")
                                 for i in range(MAX_SITE_RECORDS // 2 + 1)]})
        with self.assertRaises(SiteShapeError):
            extract_structured_offers(page(group, group))

    def test_container_and_html_size_budgets_remain_bounded(self):
        for html in (b'x' * (MAX_HTML_BYTES + 1),
                     page(*([app_data({})] * (MAX_SITE_RECORDS + 1)))):
            with self.assertRaises(SiteShapeError):
                extract_structured_offers(html)

    def test_malformed_or_deep_json_is_safe_shape_error(self):
        for raw in ('{invalid', '[' * 1200 + ']' * 1200):
            with self.assertRaises(SiteShapeError):
                extract_structured_offers(page(
                    '<app-data data-status="success">' + escape(raw) + '</app-data>'))

    def test_legacy_literal_json_remains_supported(self):
        self.assertEqual(extract_structured_offers(site_html([site_row()])),
                         [site_row()])

    def test_optional_offline_inspection_parses_only_hash_gated_bytes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "api-view-compare"
            folder.mkdir()
            path = folder / "etilbudsavis-husholdning-20261016T150436984303Z.html"
            body = page(app_data({"data": [site_row(name="Private synthetic sentinel")]}))
            path.write_bytes(body)
            with patch("socket.socket.connect", side_effect=AssertionError("HTTP forbidden")):
                result = inspect_private_html(path,
                    expected_sha256=hashlib.sha256(body).hexdigest(),
                    private_root=root, catalog_id="W8l7eqAB", parse_offers=True)
            self.assertEqual(result["parser_status"], "site_offers_recognized")
            self.assertEqual(result["recognized_offers"], 1)
            self.assertEqual(path.read_bytes(), body)
            self.assertNotIn("Private synthetic sentinel", str(result))
            self.assertEqual(result["network_requests"], 0)
            with self.assertRaises(ValueError):
                inspect_private_html(path, expected_sha256="0" * 64,
                    private_root=root, catalog_id="W8l7eqAB", parse_offers=True)

    def test_optional_offline_inspection_unknown_format_retains_safe_status(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "api-view-compare"
            folder.mkdir()
            path = folder / "etilbudsavis-husholdning-20261016T150436984303Z.html"
            body = b'<html><body>Unrecognized synthetic format</body></html>'
            path.write_bytes(body)
            result = inspect_private_html(path,
                expected_sha256=hashlib.sha256(body).hexdigest(),
                private_root=root, catalog_id="W8l7eqAB", parse_offers=True)
            self.assertEqual(result["parser_status"],
                             "saved_html_parser_shape_unrecognized")
            self.assertEqual(result["recognized_offers"], 0)
            self.assertFalse(result["rank_eligible"])


if __name__ == "__main__":
    unittest.main()
