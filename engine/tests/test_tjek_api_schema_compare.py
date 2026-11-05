"""TASK-033: no-network Tjek host equivalence probing, synthetic only."""
from __future__ import annotations

from copy import deepcopy
import unittest

from nutrivalue.providers.tjek_api_schema_compare import compare_api_views
from scripts.compare_tjek_api_views import main


def source_offer(i="synthetic-id-one", title="Tortelloni", price=35.9):
    return {
        "id": i, "heading": title, "dealer_id": "2686gD",
        "catalog_id": "week-catalog", "pricing": {
            "currency": "NOK", "price": price, "pre_price": None},
        "run_from": "2026-10-12T00:00:00+02:00",
        "run_till": "2026-10-18T23:59:59+02:00",
    }


def original(items=None):
    rows = [source_offer()] if items is None else items
    return {"fetched_at": "2026-10-16T12:00:00+02:00",
            "entries": [{
                "catalog": {"id": "week-catalog",
                            "dealer_id": "2686gD"},
                "offers": rows,
            }]}


def compare(original_body=None, response=None):
    return compare_api_views(original_body or original(),
                             catalog_id="week-catalog", dealer_id="2686gD",
                             other_response=response)


class SameTjekHostSchemaTests(unittest.TestCase):
    def test_original_alone_is_truthful_baseline_not_false_comparison(self):
        report = compare()
        self.assertEqual(report["state"], "awaiting_permitted_other_host_response")
        self.assertEqual(report["original_offers"], 1)
        self.assertIsNone(report["original_unseen_in_comparison"])
        self.assertEqual(report["http_requests"], 0)
        self.assertFalse(report["rank_eligible"])

    def test_matching_sample_reports_same_original_offer(self):
        report = compare(response=[source_offer()])
        self.assertEqual(report["state"], "compared_partial_or_complete_saved_response")
        self.assertEqual(report["comparison_offers"], 1)
        self.assertEqual(report["id_overlap"], 1)
        self.assertEqual(report["exact_identity_compatible"], 1)
        self.assertEqual(report["pricing_ore_mismatch"], 0)

    def test_extra_website_only_fields_are_not_assumed_to_exist_in_api(self):
        bare = compare(response=[source_offer()])
        self.assertNotIn("field_present:membershipPrice",
                         bare["comparison_field_counts"])
        extra = source_offer()
        extra["membershipPrice"] = 29.9
        extra["pieceCountGet"] = 3
        extra["pieceCountGetFor"] = 2
        enriched = compare(response=[extra])
        fields = enriched["comparison_field_counts"]
        self.assertEqual(fields["field_nonnull:membershipPrice"], 1)
        self.assertEqual(fields["field_nonnull:pieceCountGet"], 1)
        self.assertEqual(fields["field_nonnull:pieceCountGetFor"], 1)
        self.assertTrue(enriched["same_upstream_not_independent_price_confirmation"])

    def test_null_fields_not_counted_as_known_membership(self):
        other = source_offer()
        other["membershipPrice"] = None
        r = compare(response=[other])["comparison_field_counts"]
        self.assertEqual(r["field_present:membershipPrice"], 1)
        self.assertNotIn("field_nonnull:membershipPrice", r)

    def test_changed_price_counts_conflict_not_a_confirmation(self):
        report = compare(response=[source_offer(price=29.9)])
        self.assertEqual(report["id_overlap"], 1)
        self.assertEqual(report["pricing_ore_mismatch"], 1)
        self.assertEqual(report["exact_identity_compatible"], 0)

    def test_two_missing_prices_are_not_false_identity_matches(self):
        missing = source_offer()
        missing["pricing"]["price"] = None
        r = compare(original([missing]), response=[deepcopy(missing)])
        self.assertEqual(r["pricing_ore_mismatch"], 1)
        self.assertEqual(r["exact_identity_compatible"], 0)

    def test_timestamp_other_offset_same_instant(self):
        other = source_offer()
        other["run_from"] = "2026-10-11T22:00:00Z"
        other["run_till"] = "2026-10-18T21:59:59Z"
        self.assertEqual(compare(response=[other])["exact_identity_compatible"], 1)

    def test_different_validity_window_refused(self):
        other = source_offer()
        other["run_from"] = "2026-09-11T22:00:00Z"
        r = compare(response=[other])
        self.assertEqual(r["validity_mismatch"], 1)
        self.assertEqual(r["exact_identity_compatible"], 0)

    def test_different_heading_refused_even_if_id_matches(self):
        other = source_offer(title="Different product variant")
        r = compare(response=[other])
        self.assertEqual(r["heading_mismatch"], 1)
        self.assertEqual(r["exact_identity_compatible"], 0)

    def test_no_foreign_dealer_or_catalog_is_matched(self):
        other = source_offer()
        for patch in ({"dealer_id": "wrong-dealer"},
                      {"catalog_id": "different-week"},
                      {"catalog_id": None}):
            with self.assertRaises(ValueError):
                compare(response=[other | patch])

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):
            compare(response=[source_offer(), source_offer()])

    def test_sample_omissions_not_declared_actual_missing_offers(self):
        items = [source_offer(), source_offer(
            "synthetic-id-two", "Baconterninger", 56.9)]
        report = compare(original(items), response=[items[0]])
        self.assertEqual(report["id_overlap"], 1)
        self.assertEqual(report["original_unseen_in_comparison"], 1)
        self.assertEqual(report["state"], "compared_partial_or_complete_saved_response")

    def test_wrapped_other_snapshot_supported_when_previously_saved(self):
        body = {"offers": [source_offer()],
                "source": "approved-private-second-host-json",
                "fetched_at": "2026-10-16T12:00:00+02:00"}
        self.assertEqual(compare(response=body)["exact_identity_compatible"], 1)

    def test_no_original_catalog_is_rejected(self):
        with self.assertRaises(ValueError):
            compare_api_views(original(), catalog_id="other-catalog",
                              dealer_id="2686gD")

    def test_comparison_does_not_mutate_saved_input(self):
        left = original()
        right = [source_offer()]
        before = deepcopy((left, right))
        compare(left, right)
        self.assertEqual((left, right), before)

    def test_bare_comparison_cli_requires_paths(self):
        with self.assertRaises(SystemExit) as raised:
            main([])
        self.assertEqual(raised.exception.code, 2)

    def test_not_a_commercial_price_ranking(self):
        r = compare(response=[source_offer()])
        self.assertFalse(r["rank_eligible"])
        self.assertTrue(r["source_only"])


if __name__ == "__main__":
    unittest.main()
