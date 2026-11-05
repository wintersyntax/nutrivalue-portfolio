"""TASK-033: synthetic, no-network reproducible supplementary evidence rules."""
from __future__ import annotations

import unittest

from nutrivalue.providers.tjek_secondary_evidence import (
    diagnose_alternative_mass_basis, inspect_product_fact,
    match_etilbudsavis_view, parse_carton_condition, valid_gtin13,
)


def offer(name="Coop Tortelloni", price=35.9, *, mass_g=250,
          description="250 g. Pr pk Pr kg 119,60"):
    return {
        "id": "synthetic-offer-one",
        "heading": name,
        "dealer_id": "2686gD",
        "catalog_id": "catalog-example",
        "run_from": "2026-10-11T22:00:00+00:00",
        "run_till": "2026-10-19T21:59:59+00:00",
        "pricing": {"price": price, "currency": "NOK"},
        "description": description,
        "quantity": {
            "unit": {"symbol": "g", "si": {"symbol": "kg", "factor": 0.001}},
            "size": {"from": mass_g, "to": mass_g},
            "pieces": {"from": 1, "to": 1}
        },
    }


def same_provider_view(name="Coop Tortelloni", price=35.9, member=29.9):
    return {
        "name": name, "currencyCode": "NOK", "price": price,
        "membershipPrice": member,
        "businessPublicId": "2686gD",
        "publicationPublicId": "catalog-example",
        "validFrom": "2026-10-11T22:00:00+0000",
        "validUntil": "2026-10-18T21:59:59+0000",
        "pieceCountGet": None,
        "pieceCountGetFor": None,
        "relativeSavings": None,
        "membershipRelativeSavings": None,
    }


class EtViewTests(unittest.TestCase):
    def test_same_publisher_matkroken_candidate_is_not_verified(self):
        result = match_etilbudsavis_view(offer(), same_provider_view())
        self.assertEqual(result["state"], "correlated_same_tjek_provider_review_only")
        self.assertEqual(result["membership_price_review_only_ore"], 2990)
        self.assertFalse(result["rank_eligible"])
        self.assertFalse(result["checkout_verified"])

    def test_bacon_member_candidate_exact_own_source_price(self):
        result = match_etilbudsavis_view(
            offer("Coop Baconterninger", 56.9, mass_g=200),
            same_provider_view("Coop Baconterninger", 56.9, member=39.9))
        self.assertEqual(result["membership_price_review_only_ore"], 3990)

    def test_mismatch_of_dealer_catalog_title_price_or_currency(self):
        orig = offer()
        base = same_provider_view()
        for edited, reason in (
            ({"businessPublicId": "other"}, "uncorrelated_other_provider_view"),
            ({"publicationPublicId": "other"}, "uncorrelated_other_provider_view"),
            ({"name": "Coop Tortelloni OTHER"}, "same_catalog_name_review_only"),
            ({"price": 12}, "same_catalog_price_conflict_review_only"),
            ({"currencyCode": "DKK"}, "same_catalog_price_conflict_review_only"),
        ):
            self.assertEqual(match_etilbudsavis_view(
                orig, base | edited)["state"], reason)
            self.assertIsNone(match_etilbudsavis_view(
                orig, base | edited)["membership_price_review_only_ore"])

    def test_public_view_without_time_or_wrong_week_refused(self):
        base = same_provider_view()
        missing = match_etilbudsavis_view(offer(), base | {"validUntil": None})
        self.assertEqual(missing["state"], "missing_or_invalid_validity_review_only")
        stale = match_etilbudsavis_view(offer(), base | {
            "validFrom": "2026-09-10T00:00:00+00:00",
            "validUntil": "2026-09-11T23:59:59+00:00",
        })
        self.assertEqual(stale["state"], "validity_conflict_review_only")

    def test_explicit_member_above_regular_not_adopted(self):
        result = match_etilbudsavis_view(
            offer(), same_provider_view(member=40))
        self.assertEqual(result["state"],
                         "member_tier_invalid_or_already_discounted_review_only")
        self.assertIsNone(result["membership_price_review_only_ore"])

    def test_tier_amount_must_be_exact_ore_not_rounded_down(self):
        view = same_provider_view(member=29.901)
        self.assertIsNone(
            match_etilbudsavis_view(offer(), view)["membership_price_review_only_ore"])

    def test_same_source_only_percentage_never_subtracted(self):
        view = same_provider_view(member=None) | {
            "membershipRelativeSavings": 25,
            "relativeSavings": 30,
        }
        result = match_etilbudsavis_view(offer(), view)
        self.assertTrue(result["percentage_scope_review_required"])
        self.assertIsNone(result["membership_price_review_only_ore"])

    def test_three_for_two_is_pay_count_not_kr_total(self):
        # Public eTilbudsavis example: '3 FOR 2' is 3 received / 2 paid,
        # NOT a 2 kr price for three units.
        view = same_provider_view(member=None) | {
            "pieceCountGet": 3,
            "pieceCountGetFor": 2,
        }
        result = match_etilbudsavis_view(offer(), view)
        self.assertEqual(result["buy_units_review_only"], 3)
        self.assertEqual(result["pay_units_review_only"], 2)
        self.assertIsNone(result["bundle_total_review_only_ore"])
        self.assertFalse(result["rank_eligible"])

    def test_legacy_two_for_45_cannot_be_read_as_get_pay_pair(self):
        # These fields cannot encode '2 for NOK 45'. A separately printed
        # source or PDF total must establish that monetary condition.
        view = same_provider_view(price=45, member=None) | {
            "pieceCountGet": 2, "pieceCountGetFor": 45,
        }
        result = match_etilbudsavis_view(offer(price=45), view)
        self.assertIsNone(result["buy_units_review_only"])
        self.assertIsNone(result["pay_units_review_only"])
        self.assertIsNone(result["bundle_total_review_only_ore"])

    def test_ambiguous_or_free_item_count_kept_as_non_price_evidence(self):
        view = same_provider_view(member=None) | {
            "pieceCountGet": 2, "pieceCountGetFor": 1,
        }
        result = match_etilbudsavis_view(offer(), view)
        self.assertEqual((result["buy_units_review_only"],
                          result["pay_units_review_only"]), (2, 1))
        self.assertFalse(result["checkout_verified"])
        self.assertIsNone(result["bundle_total_review_only_ore"])
        invalid = match_etilbudsavis_view(offer(), view | {
            "pieceCountGet": 1, "pieceCountGetFor": 2})
        self.assertIsNone(invalid["buy_units_review_only"])

    def test_matching_original_offer_id_preferred_and_mismatches_fail(self):
        view = same_provider_view() | {"publicId": "synthetic-offer-one"}
        matching = match_etilbudsavis_view(offer(), view)
        self.assertEqual(matching["state"],
                         "correlated_same_tjek_provider_review_only")
        foreign = match_etilbudsavis_view(
            offer(), view | {"publicId": "some-other-offer"})
        self.assertEqual(foreign["state"],
                         "same_catalog_offer_id_conflict_review_only")
        self.assertIsNone(foreign["membership_price_review_only_ore"])

    def test_empty_percent_or_quantity_fields_not_inferred(self):
        result = match_etilbudsavis_view(offer(), same_provider_view(member=None))
        self.assertIsNone(result["buy_units_review_only"])
        self.assertIsNone(result["pay_units_review_only"])
        self.assertFalse(result["percentage_scope_review_required"])

    def test_malformed_input_refused(self):
        with self.assertRaises(ValueError):
            match_etilbudsavis_view([], {})


class CartonTests(unittest.TestCase):
    def test_explicit_five_kg_carton_basket(self):
        orig = offer("Frosne reker", 149, mass_g=1000,
                     description="Pr kg 149. Kartong 5 kg for 699")
        result = parse_carton_condition(orig)
        self.assertEqual(result["state"], "explicit_carton_basket_review_only")
        self.assertEqual(result["minimum_carton_kg"], "5")
        self.assertEqual(result["carton_total_review_only_ore"], 69900)
        self.assertEqual(result["carton_ore_per_kg_review_only"], 13980)
        self.assertFalse(result["rank_eligible"])

    def test_single_kg_without_carton_is_not_bulk_price(self):
        self.assertEqual(
            parse_carton_condition(offer(description="Frosne reker 149 pr kg"))["state"],
            "no_explicit_carton_total")

    def test_untrusted_former_carton_total_not_active(self):
        result = parse_carton_condition(
            offer(description="Førpris kartong 5 kg for 699"))
        self.assertEqual(result["state"], "carton_reference_not_promotion")

    def test_two_competing_carton_totals_marked_ambiguous(self):
        result = parse_carton_condition(
            offer(description="Kartong 5 kg for 699, kartong 4 kg for 550"))
        self.assertEqual(result["state"], "ambiguous_carton_mentions")

    def test_carton_condition_does_not_subtract_original_price(self):
        orig = offer(price=149, description="Kartong 5 kg for 699")
        result = parse_carton_condition(orig)
        self.assertEqual(orig["pricing"]["price"], 149)
        self.assertFalse(result["rank_eligible"])


class ProductFactsTests(unittest.TestCase):
    def test_kassalapp_gtin_checksum(self):
        self.assertTrue(valid_gtin13("7039010019828"))
        self.assertFalse(valid_gtin13("7039010019829"))
        self.assertFalse(valid_gtin13("703901001982"))
        self.assertFalse(valid_gtin13(7039010019828))

    def test_kassalapp_name_search_never_assigns_gtin_by_itself(self):
        fact = {"source": "kassalapp", "ean": "7039010019828",
                "weight": 500, "weight_unit": "g", "name": "Demo pizza"}
        result = inspect_product_fact(fact)
        self.assertEqual(result["state"], "unlinked_gtin_product_candidate")
        self.assertIsNone(result["declared_pack_g"])

    def test_exact_owner_approved_gtin_can_link_pack_mass_not_edible_mass(self):
        fact = {"source": "kassalapp", "ean": "7039010019828",
                "weight": 500, "weight_unit": "g",
                "reviewer_confirmed": True,
                "source_url": "https://example.org/synthetic-product",
                "checked_on": "2026-10-16"}
        result = inspect_product_fact(
            fact, approved_offer_gtin="7039010019828")
        self.assertEqual(result["state"], "attested_exact_gtin_product_fact_review_only")
        self.assertEqual(result["declared_pack_g"], "500")
        self.assertIsNone(result["drained_mass_g"])
        self.assertFalse(result["rank_eligible"])

    def test_wrong_approved_gtin_cannot_link(self):
        fact = {"source": "kassalapp", "ean": "7039010019828",
                "weight": 500, "weight_unit": "g",
                "reviewer_confirmed": True,
                "source_url": "https://example.org/synthetic-product",
                "checked_on": "2026-10-16"}
        result = inspect_product_fact(fact, approved_offer_gtin="0000000000000")
        self.assertEqual(result["state"], "unlinked_gtin_product_candidate")

    def test_gtin_alone_without_review_url_and_date_cannot_attest(self):
        fact = {"source": "kassalapp", "ean": "7039010019828",
                "weight": 500, "weight_unit": "g",
                "reviewer_confirmed": True}
        result = inspect_product_fact(
            fact, approved_offer_gtin="7039010019828")
        self.assertEqual(result["state"], "unlinked_gtin_product_candidate")
        self.assertIsNone(result["declared_pack_g"])

    def test_only_attested_manufacturer_can_supply_drained_g(self):
        fact = {"source": "manufacturer", "ean": "7039010019828",
                "weight": 85, "weight_unit": "g",
                "drained_weight_g": 60, "reviewer_confirmed": True,
                "source_url": "https://example.org/synthetic-product",
                "checked_on": "2026-10-16"}
        result = inspect_product_fact(fact, approved_offer_gtin="7039010019828")
        self.assertEqual(result["drained_mass_g"], "60")
        other = inspect_product_fact(
            fact | {"source": "kassalapp"},
            approved_offer_gtin="7039010019828")
        self.assertIsNone(other["drained_mass_g"])

    def test_drained_g_cannot_exceed_pack_mass(self):
        fact = {"source": "manufacturer", "ean": "7039010019828",
                "weight": 85, "weight_unit": "g",
                "drained_weight_g": 200, "reviewer_confirmed": True,
                "source_url": "https://example.org/synthetic-product",
                "checked_on": "2026-10-16"}
        self.assertIsNone(inspect_product_fact(
            fact, approved_offer_gtin="7039010019828")["drained_mass_g"])


class AlternativeMassTests(unittest.TestCase):
    def test_tuna_mismatch_yields_unverified_60g_hypothesis(self):
        orig = offer("Synthetic tuna", 13.9, mass_g=85,
                     description="I olje. 85 g. Pr kg 231,67")
        result = diagnose_alternative_mass_basis(orig)
        self.assertEqual(result["state"],
                         "arithmetic_smaller_mass_hypothesis_review_only")
        self.assertEqual(result["inferred_grams_review_only"], 60)
        self.assertFalse(result["rank_eligible"])

    def test_4g_stick_does_not_claim_actual_mass_if_larger_than_pack(self):
        orig = offer("Synthetic stick", 20, mass_g=4.25,
                     description="4,25g. Pr kg 5000,00")
        result = diagnose_alternative_mass_basis(orig)
        self.assertEqual(result["inferred_grams_review_only"], 4)
        self.assertEqual(result["state"],
                         "arithmetic_smaller_mass_hypothesis_review_only")

    def test_no_rate_or_matching_mass_no_new_hypothesis(self):
        self.assertEqual(
            diagnose_alternative_mass_basis(offer(description="No unit rate"))["state"],
            "no_arithmetic_alternative_mass")
        self.assertEqual(
            diagnose_alternative_mass_basis(offer(
                price=20, mass_g=100, description="Pr kg 200,00"))["state"],
            "no_arithmetic_alternative_mass")

    def test_multiple_unit_rates_are_not_one_inferred_mass(self):
        orig = offer(price=20, mass_g=100,
                     description="Pr kg 200,00 / Pr kg 250,00")
        self.assertIsNone(diagnose_alternative_mass_basis(
            orig)["inferred_grams_review_only"])


if __name__ == "__main__":
    unittest.main()
