"""TASK-033: deterministic, synthetic-only offer-condition tests; never HTTP."""
from __future__ import annotations

import unittest

from nutrivalue.providers.tjek_offer_conditions import interpret_offer


def offer(description="", price=199, prior=None, heading="Example"):
    return {
        "heading": heading, "description": description,
        "pricing": {"currency": "NOK", "price": price, "pre_price": prior},
    }


class OfferConditionsTests(unittest.TestCase):
    def test_member_saving_with_explicit_cue(self):
        got = interpret_offer(offer("Medlemskupp! Ikke medlem: 199 Spar kr 70"))
        self.assertEqual(got["conditional_member_ore"], 12900)
        self.assertEqual(got["explicit_nonmember_ore"], 19900)
        self.assertEqual(got["saving_ore"], 7000)
        self.assertEqual(got["member_evidence"], "member_cue_and_saving_arithmetic")
        self.assertEqual(got["price_kind"], "advertised_nonmember")
        self.assertFalse(got["rank_eligible"])

    def test_nonmember_minus_saving_is_review_only_without_positive_membership(self):
        got = interpret_offer(offer("Ikke-medlem: 199 Spar: 70"))
        self.assertEqual(got["review_only_possible_member_ore"], 12900)
        self.assertIsNone(got["conditional_member_ore"])
        self.assertIn("member_eligibility_not_explicit", got["review_reasons"])

    def test_norwegian_decimal_savings_exact_ore(self):
        got = interpret_offer(offer("Ikke medlem: 64,50 Spar kr 32,25", 64.5))
        self.assertEqual(got["review_only_possible_member_ore"], 3225)
        self.assertEqual(got["explicit_nonmember_ore"], 6450)

    def test_explicit_member_is_not_double_discounted(self):
        got = interpret_offer(offer("Medlemspris 49,90. Spar kr 10,00", 49.9))
        self.assertEqual(got["conditional_member_ore"], 4990)
        self.assertEqual(got["price_kind"], "advertised_member")
        self.assertIsNone(got["review_only_possible_member_ore"])

    def test_three_for_ninety_is_bundle_not_one_item(self):
        got = interpret_offer(offer(
            "coop MEDLEMS-KUPP!\n3 for 90\nIkke-medlem? Se hyllepris\n1 stk 49,90",
            90, heading="Chocolate"))
        self.assertEqual(got["bundle_units"], 3)
        self.assertEqual(got["bundle_total_ore"], 9000)
        self.assertEqual(got["bundle_unit_equivalent_ore"], 3000)
        self.assertEqual(got["price_kind"], "advertised_bundle_total")
        self.assertIsNone(got["conditional_member_ore"])
        self.assertFalse(got["rank_eligible"])

    def test_indivisible_bundle_rate_not_fabricated(self):
        got = interpret_offer(offer("3 for 100", 100))
        self.assertEqual(got["bundle_units"], 3)
        self.assertIsNone(got["bundle_unit_equivalent_ore"])
        self.assertEqual(got["bundle_total_ore"], 10000)

    def test_bundle_conflict_requires_review(self):
        got = interpret_offer(offer("2 for 60", 45))
        self.assertIn("source_bundle_mismatch", got["review_reasons"])

    def test_former_price_is_not_a_current_member_offer(self):
        got = interpret_offer(offer("Førpris 79,90. Pr kg 60", 59.9, 79.9))
        self.assertEqual(got["source_pre_price_ore"], 7990)
        self.assertEqual(got["explicit_previous_ore"], 7990)
        self.assertIsNone(got["conditional_member_ore"])

    def test_percent_discount_with_no_product_baseline_not_computed(self):
        got = interpret_offer(offer("25 % rabatt på utvalgte varer", 100))
        self.assertEqual(got["percent_condition_count"], 1)
        self.assertIn("percentage_needs_item_base_and_scope", got["review_reasons"])
        self.assertIsNone(got["conditional_member_ore"])

    def test_material_composition_percent_is_not_discount(self):
        got = interpret_offer(offer("100 % ull. Pr stk", 299))
        self.assertEqual(got["percent_condition_count"], 0)

    def test_pant_does_not_assume_checkout_price(self):
        got = interpret_offer(offer("1 l + pant", 20))
        self.assertTrue(got["pant_mentioned"])
        self.assertIn("pant_checkout_total_not_verified", got["review_reasons"])

    def test_spar_fra_is_not_a_fixed_saving(self):
        got = interpret_offer(offer("Spar kr fra 20,00. Førpris fra 90,00", 49))
        self.assertIsNone(got["saving_ore"])
        self.assertIsNone(got["conditional_member_ore"])

    def test_conflicting_savings_are_not_selected(self):
        got = interpret_offer(offer(
            "Medlemskupp. Ikke-medlem: 199. Spar kr 70. Spar kr 80", 199))
        self.assertIn("ambiguous_multiple_conditions", got["review_reasons"])
        self.assertIsNone(got["conditional_member_ore"])

    def test_already_member_source_is_not_discounted_twice(self):
        got = interpret_offer(offer("Medlemskupp. Ikke-medlem: 199. Spar kr 70", 129))
        self.assertEqual(got["conditional_member_ore"], 12900)
        self.assertEqual(got["price_kind"], "advertised_member_derived")
        self.assertEqual(got["member_evidence"], "member_cue_saving_already_in_source")

    def test_nonmember_source_mismatch_is_not_subtracted(self):
        got = interpret_offer(offer("Medlemskupp. Ikke-medlem: 199. Spar kr 70", 130))
        self.assertIn("source_nonmember_mismatch", got["review_reasons"])
        self.assertIsNone(got["conditional_member_ore"])

    def test_explicit_member_can_differ_from_headline_source(self):
        got = interpret_offer(offer("Medlemspris: 129", 199))
        self.assertEqual(got["conditional_member_ore"], 12900)
        self.assertEqual(got["source_price_ore"], 19900)
        self.assertFalse(got["rank_eligible"])

    def test_both_member_and_nonmember_advertised(self):
        got = interpret_offer(offer("Medlemspris 129. Ikke-medlem: 199", 129))
        self.assertEqual(got["conditional_member_ore"], 12900)
        self.assertEqual(got["explicit_nonmember_ore"], 19900)
        self.assertEqual(got["price_kind"], "advertised_member")

    def test_plain_spar_amount_is_recognized(self):
        got = interpret_offer(offer("Ikke-medlem: 199 Spar 70", 199))
        self.assertEqual(got["review_only_possible_member_ore"], 12900)

    def test_member_bundle_has_member_eligibility_and_minimum_quantity(self):
        got = interpret_offer(offer("Medlemskupp 3 for 90", 90))
        self.assertTrue(got["bundle_member_only"])
        self.assertEqual(got["bundle_units"], 3)
        self.assertIsNone(got["conditional_member_ore"])

    def test_member_equal_or_greater_than_nonmember_rejected(self):
        got = interpret_offer(offer("Medlemspris: 199. Ikke medlem: 100", 100))
        self.assertIn("member_not_below_nonmember", got["review_reasons"])
        self.assertIsNone(got["conditional_member_ore"])

    def test_source_only_and_unknown_kept(self):
        got = interpret_offer(offer("Vanlig helgepris", 48.9))
        self.assertEqual(got["price_kind"], "unclassified_advertised")
        self.assertEqual(got["source_status"], "source_only")
        self.assertFalse(got["checked_shop_price"])

    def test_malformed_or_foreign_currency_rejected(self):
        with self.assertRaises(ValueError):
            interpret_offer(offer("x", 10) | {"pricing": {"currency": "EUR", "price": 10}})
        with self.assertRaises(ValueError):
            interpret_offer({"description": 13, "heading": "x",
                             "pricing": {"currency": "NOK", "price": 10}})


if __name__ == "__main__":
    unittest.main()
