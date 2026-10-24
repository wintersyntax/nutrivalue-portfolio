"""TASK-033: source-only, deterministic per-kg/l calculation safety tests."""
from __future__ import annotations

import unittest

from nutrivalue.providers.tjek_unit_price import (
    _printed, interpret_unit_price,
    condition_unit_prices_from_pdf_bundles, pdf_bundle_unit_summary,
    mismatch_clue_bucket,
)


def offer(price=79.9, *, amount=100, unit="g", pieces=4,
          description="Pr kg 199,75", size_to=None, piece_to=None):
    basis = "l" if unit in {"l", "dl", "cl", "ml"} else "kg"
    factor = {"g": 0.001, "kg": 1, "hg": 0.1, "l": 1,
              "dl": 0.1, "cl": 0.01, "ml": 0.001}[unit]
    return {
        "heading": "Synthetic multipack",
        "description": description,
        "pricing": {"price": price, "currency": "NOK", "pre_price": None},
        "quantity": {
            "unit": {"symbol": unit, "si": {"symbol": basis, "factor": factor}},
            "size": {"from": amount, "to": amount if size_to is None else size_to},
            "pieces": {"from": pieces, "to": pieces if piece_to is None else piece_to},
        },
    }


class UnitPriceTests(unittest.TestCase):
    def test_regular_hg_price_is_not_the_current_friday_offer_rate(self):
        # Real-source shape: Nidar weighed candy, 10 NOK / 100g, while
        # 'Ord. pris 18,50 pr. hg' describes the ordinary baseline.
        row = offer(price=10, amount=100, unit="g", pieces=1,
                    description="Ord. pris 18,50 pr. hg. "
                    "Tilbudet gjelder hver fredag fra 7. august.")
        result = interpret_unit_price(row)
        self.assertEqual(_printed(row["description"]), [])
        self.assertEqual(result["state"], "ordinary_or_previous_reference_unit_only")
        self.assertEqual(result["computed_ore_per_basis_review_only"], 10000)
        self.assertEqual(result["source_printed_candidates"], 0)
        self.assertFalse(result["rank_eligible"])

    def test_ordinary_price_skipped_but_current_unit_rate_still_recognized(self):
        description = "Ord. pris 18,50 pr. hg. Tilbud. 10,00 pr. hg"
        self.assertEqual(_printed(description), [("kg", 10000, False)])
        row = offer(price=10, amount=100, unit="g", pieces=1,
                    description=description)
        self.assertEqual(
            interpret_unit_price(row)["state"],
            "source_text_unit_rate_consistent_review_only")

    def test_old_reference_not_mistaken_for_present_rate_when_labeled_forpris(self):
        description = "Førpris 158,64 pr. kg. Nå kun 39,90 pr. kg"
        self.assertEqual(_printed(description), [("kg", 3990, False)])

    def test_rema_two_endpoint_rate_range_is_never_one_exact_rate(self):
        # Real-source shape: 750g at 29,90 NOK, printed
        # 39,87-53,20 kr/kg for several bread varieties.
        description = "Mesterbakeren, 750 g, 39,87-53,20 pr. kg"
        self.assertEqual(_printed(description),
                         [("kg", 3987, True), ("kg", 5320, True)])
        row = offer(price=29.9, amount=750, unit="g", pieces=1,
                    description=description)
        result = interpret_unit_price(row)
        self.assertEqual(result["computed_ore_per_basis_review_only"], 3987)
        self.assertEqual(result["state"], "printed_range_or_lower_bound_review")
        self.assertFalse(result["rank_eligible"])

    def test_rema_unicode_en_dash_printed_range_remains_review_only(self):
        description = "39,87–53,20 pr. kg"
        self.assertEqual(_printed(description),
                         [("kg", 3987, True), ("kg", 5320, True)])
        self.assertEqual(
            interpret_unit_price(offer(
                price=29.9, amount=750, unit="g", pieces=1,
                description=description))["state"],
            "printed_range_or_lower_bound_review")

    def test_fra_80_g_overrides_exact_api_size_for_group_pack(self):
        # Real-source shape: Bunnpris mixed sliced meat family 'Fra 80 g'.
        row = offer(price=20, amount=80, unit="g", pieces=1,
                    description="Fra 80 g pr. pk 181.82 /KG")
        result = interpret_unit_price(row)
        self.assertEqual(result["state"], "pack_size_minimum_in_description")
        self.assertIsNone(result["computed_ore_per_basis_review_only"])
        self.assertIsNone(result["pack_quantity_basis_units"])
        self.assertFalse(result["rank_eligible"])

    def test_fra_80_g_does_not_clobber_unrelated_litre_unit_or_date(self):
        milk = offer(price=20, amount=1, unit="l", pieces=1,
                     description="Fra 80 g kylling. Pr l 20,00")
        self.assertEqual(
            interpret_unit_price(milk)["state"],
            "source_text_unit_rate_consistent_review_only")
        chips = offer(price=10, amount=100, unit="g", pieces=1,
                      description="Tilbudet gjelder hver fredag fra 7. august. "
                      "Pr kg 100,00")
        self.assertEqual(
            interpret_unit_price(chips)["state"],
            "source_text_unit_rate_consistent_review_only")

    def test_rema_number_before_pr_kg_ignores_following_pack_price(self):
        got = interpret_unit_price(offer(
            price=39.66, amount=250, pieces=1,
            description="158,64 pr. kg 39,66 PR. STK"))
        self.assertEqual(got["state"], "source_text_unit_rate_consistent_review_only")
        self.assertEqual(got["computed_ore_per_basis_review_only"], 15864)
        self.assertEqual(got["source_printed_candidates"], 1)
        self.assertEqual(_printed("158,64 pr. kg 39,66 PR. STK"),
                         [("kg", 15864, False)])

    def test_rema_number_before_pr_l(self):
        got = interpret_unit_price(offer(
            price=29.9, amount=1, unit="l", pieces=1,
            description="29,90 pr. l 29,90 pr. stk"))
        self.assertEqual(got["state"], "source_text_unit_rate_consistent_review_only")

    def test_before_unit_historical_price_not_current(self):
        self.assertEqual(_printed("Førpris 158,64 pr. kg"), [])
        self.assertEqual(_printed("Før 158,64 pr. kg"), [])

    def test_before_unit_does_not_parse_percent_or_multipliers(self):
        self.assertEqual(_printed("25 % rabatt på varene. 2 x 150 g."), [])
        self.assertEqual(_printed("2 pr. kg x ca. 1 kg"), [])

    def test_ambiguous_two_printed_rates_withhold_consistency(self):
        got = interpret_unit_price(offer(
            price=39.66, amount=250, pieces=1,
            description="158,64 pr. kg / 199,75 pr. kg"))
        self.assertEqual(got["state"], "printed_range_or_lower_bound_review")

    def test_before_unit_source_and_after_unit_other_rate_reviews(self):
        got = interpret_unit_price(offer(
            price=39.66, amount=250, pieces=1,
            description="158,64 pr. kg. Pr kg 100,00"))
        self.assertEqual(got["state"], "printed_range_or_lower_bound_review")

    def test_pdf_bundle_join_replaces_false_one_item_interpretation(self):
        source = offer(price=45, amount=170, pieces=1,
                       description="Pr kg 132,35")
        source.update({"id": "synthetic-a", "catalog_page": 3})
        badge = {
            "source_offer_id": "synthetic-a", "source_catalog_page": 3,
            "badge_state": "geometry_total_correlated_review_only",
            "source_total_recorroborated": True, "bundle_units_candidate": 2,
            "source_status": "source_only", "rank_eligible": False,
        }
        [row] = condition_unit_prices_from_pdf_bundles([source], [badge])
        self.assertEqual(row["state_before"], "unexplained_unit_rate_mismatch")
        self.assertEqual(row["state_after"], "conditional_bundle_rate_review_only")
        self.assertEqual(row["review_only_unit_ore"], 13235)
        self.assertTrue(row["new_pdf_bundle_condition"])
        self.assertFalse(row["rank_eligible"])
        counts = pdf_bundle_unit_summary([row])
        self.assertEqual(counts["pdf_bundle_conditions_correlated_review_only"], 1)
        self.assertEqual(counts["pdf_bundle_unit_rates_computable_review_only"], 1)
        self.assertEqual(counts["new_verified_shop_unit_prices"], 0)

    def test_pdf_join_cannot_treat_nonmatching_badge_as_evidence(self):
        source = offer(price=45, amount=170, pieces=1,
                       description="Pr kg 132,35")
        source.update({"id": "synthetic-b", "catalog_page": 1})
        weak = {
            "source_offer_id": "synthetic-b", "source_catalog_page": 1,
            "badge_state": "geometry_without_total_review",
            "bundle_units_candidate": None,
            "source_total_recorroborated": False,
        }
        [row] = condition_unit_prices_from_pdf_bundles([source], [weak])
        self.assertFalse(row["pdf_condition_correlated"])
        self.assertEqual(row["state_before"], row["state_after"])
        self.assertFalse(row["new_pdf_bundle_condition"])

    def test_pdf_join_refuses_forged_flag_or_wrong_page(self):
        source = offer(price=45, amount=170, pieces=1)
        source.update({"id": "synthetic-c", "catalog_page": 1})
        positive = {
            "source_offer_id": "synthetic-c", "source_catalog_page": 1,
            "badge_state": "geometry_total_correlated_review_only",
            "source_total_recorroborated": True,
            "bundle_units_candidate": 2, "source_status": "source_only",
            "rank_eligible": False,
        }
        with self.assertRaises(ValueError):
            condition_unit_prices_from_pdf_bundles([source],
                                                   [positive | {"source_catalog_page": 2}])
        with self.assertRaises(ValueError):
            condition_unit_prices_from_pdf_bundles([source],
                                                   [positive | {"source_total_recorroborated": False}])
        with self.assertRaises(ValueError):
            condition_unit_prices_from_pdf_bundles([source],
                                                   [positive | {"bundle_units_candidate": 1}])

    def test_pdf_join_keeps_size_range_unavailable(self):
        source = offer(price=45, amount=170, pieces=1, size_to=250)
        source.update({"id": "synthetic-d", "catalog_page": 3})
        badge = {
            "source_offer_id": "synthetic-d", "source_catalog_page": 3,
            "badge_state": "geometry_total_correlated_review_only",
            "source_total_recorroborated": True, "bundle_units_candidate": 2,
            "source_status": "source_only", "rank_eligible": False,
        }
        [row] = condition_unit_prices_from_pdf_bundles([source], [badge])
        self.assertEqual(row["state_after"], "pack_size_range")
        self.assertIsNone(row["review_only_unit_ore"])
        self.assertEqual(pdf_bundle_unit_summary([row])
                         ["pdf_bundle_unit_rates_unavailable"], 1)

    def test_mismatch_clues_twofold_does_not_verify_bundle(self):
        row = offer(price=45, amount=170, pieces=1,
                    description="Pr kg 132,35")
        parsed = interpret_unit_price(row)
        self.assertEqual(parsed["state"], "unexplained_unit_rate_mismatch")
        self.assertEqual(
            mismatch_clue_bucket(row, parsed),
            "computed_about_twice_printed_possible_bundle_or_pack")
        self.assertFalse(parsed["rank_eligible"])

    def test_mismatch_clues_small_gap_and_unknown(self):
        near = offer(price=40, amount=200, pieces=1,
                     description="Pr kg 197,00")
        parsed = interpret_unit_price(near)
        self.assertEqual(parsed["state"], "unexplained_unit_rate_mismatch")
        self.assertEqual(mismatch_clue_bucket(near, parsed),
                         "small_gap_above_half_percent_review")
        unknown = offer(price=40, amount=200, pieces=1,
                        description="Pr kg 99,99")
        self.assertEqual(
            mismatch_clue_bucket(unknown, interpret_unit_price(unknown)),
            "computed_about_twice_printed_possible_bundle_or_pack")
        no_mismatch = offer()
        self.assertEqual(
            mismatch_clue_bucket(no_mismatch, interpret_unit_price(no_mismatch)),
            "not_an_unexplained_mismatch")

    def test_no_mismatch_clue_contains_original_numeric_amount_or_name(self):
        row = offer(price=40, amount=200, pieces=1,
                    description="Pr kg 99,99")
        clue = mismatch_clue_bucket(row, interpret_unit_price(row))
        self.assertNotIn("99,99", clue)
        self.assertNotIn("Synthetic multipack", clue)

    def test_four_times_100g_matches_printed_kg(self):
        got = interpret_unit_price(offer())
        self.assertEqual(got["basis"], "kg")
        self.assertEqual(got["pack_quantity_basis_units"], "0.400")
        self.assertEqual(got["computed_ore_per_basis_review_only"], 19975)
        self.assertEqual(got["state"], "source_text_unit_rate_consistent_review_only")
        self.assertFalse(got["rank_eligible"])
        self.assertEqual(got["printed_origin"], "tjek_description_not_independent_pdf")

    def test_two_times_150g_rounds_to_printed_kr_per_kg(self):
        got = interpret_unit_price(offer(
            price=59.9, amount=150, pieces=2, description="Pr kg 199,67"))
        self.assertEqual(got["computed_ore_per_basis_review_only"], 19967)
        self.assertEqual(got["state"], "source_text_unit_rate_consistent_review_only")

    def test_six_times_one_point_five_litres(self):
        got = interpret_unit_price(offer(
            price=89, amount=1.5, unit="l", pieces=6,
            description="Pr l 9,89"))
        self.assertEqual(got["pack_quantity_basis_units"], "9.0")
        self.assertEqual(got["computed_ore_per_basis_review_only"], 989)
        self.assertEqual(got["state"], "source_text_unit_rate_consistent_review_only")

    def test_printed_100g_price_scales_to_per_kilogram(self):
        got = interpret_unit_price(offer(
            price=39.9, amount=200, pieces=2,
            description="Pr 100 g 9,975"))
        self.assertEqual(got["source_printed_candidates"], 0)
        self.assertEqual(got["state"], "no_comparable_printed_unit_price")

    def test_printed_per_100g_with_two_decimal(self):
        got = interpret_unit_price(offer(
            price=40, amount=200, pieces=2,
            description="Pr 100 g 10,00"))
        self.assertEqual(got["computed_ore_per_basis_review_only"], 10000)
        self.assertEqual(got["state"], "source_text_unit_rate_consistent_review_only")

    def test_printed_hg_suffix_scaling(self):
        got = interpret_unit_price(offer(
            price=30, amount=0.5, unit="kg", pieces=1,
            description="6,00/hg"))
        self.assertEqual(got["computed_ore_per_basis_review_only"], 6000)
        self.assertEqual(got["state"], "source_text_unit_rate_consistent_review_only")

    def test_no_multiplier_implicitly_assumed_as_sale(self):
        got = interpret_unit_price(offer(
            price=45, amount=170, pieces=1,
            description="Pr kg 132,35"))
        self.assertEqual(got["state"], "unexplained_unit_rate_mismatch")
        self.assertFalse(got["rank_eligible"])

    def test_pdf_correlated_two_for_uses_total_not_each(self):
        got = interpret_unit_price(offer(
            price=45, amount=170, pieces=1,
            description="Pr kg 132,35"),
            bundle_units=2, pdf_bundle_total_correlated=True)
        self.assertEqual(got["bundle_units"], 2)
        self.assertEqual(got["computed_ore_per_basis_review_only"], 13235)
        self.assertEqual(got["state"], "conditional_bundle_rate_review_only")
        self.assertFalse(got["checkout_verified"])

    def test_unverified_bundle_units_rejected(self):
        with self.assertRaises(ValueError):
            interpret_unit_price(offer(), bundle_units=2)
        with self.assertRaises(ValueError):
            interpret_unit_price(offer(), bundle_units=1,
                                 pdf_bundle_total_correlated=True)

    def test_explicit_three_for_in_api_respected(self):
        got = interpret_unit_price(offer(
            price=90, amount=200, pieces=1,
            description="3 for 90; Pr kg 150,00"))
        self.assertEqual(got["bundle_units"], 3)
        self.assertEqual(got["state"], "conditional_bundle_rate_review_only")
        self.assertEqual(got["computed_ore_per_basis_review_only"], 15000)

    def test_size_range_is_not_equated_to_one_sku(self):
        got = interpret_unit_price(offer(size_to=250))
        self.assertEqual(got["state"], "pack_size_range")
        self.assertIsNone(got["computed_ore_per_basis_review_only"])

    def test_piece_range_does_not_guess_one_quantity(self):
        got = interpret_unit_price(offer(piece_to=5))
        self.assertEqual(got["state"], "pieces_range_or_invalid")

    def test_conflicting_si_factor_refused(self):
        row = offer()
        row["quantity"]["unit"]["si"]["factor"] = 1
        self.assertEqual(interpret_unit_price(row)["state"], "si_unit_conflict")

    def test_printed_from_is_not_one_exact_rate(self):
        got = interpret_unit_price(offer(
            description="Fra 199,75/kg"))
        self.assertEqual(got["state"], "printed_range_or_lower_bound_review")

    def test_printed_history_not_used_as_current_unit_rate(self):
        candidates = _printed("Førpris 199,75/kg\nPr pk 50")
        self.assertEqual(candidates, [])

    def test_percent_or_multiplier_not_price(self):
        self.assertEqual(_printed("Pr kg 2 x ca. 1 kg, -25 %"), [])

    def test_without_printed_value_remains_uncorroborated(self):
        got = interpret_unit_price(offer(description="Normal advertised price"))
        self.assertEqual(got["state"], "no_comparable_printed_unit_price")

    def test_member_price_tier_does_not_force_mismatch_acceptance(self):
        got = interpret_unit_price(offer(
            price=199, amount=100, pieces=4,
            description="Ikke-medlem: 199 Spar kr 70. Pr kg 322,50"))
        self.assertEqual(got["state"], "mixed_member_tiers_review")
        self.assertFalse(got["rank_eligible"])

    def test_no_zero_or_invalid_prices(self):
        got = interpret_unit_price(offer(price=0))
        self.assertEqual(got["state"], "missing_or_invalid_price")
        with self.assertRaises(ValueError):
            interpret_unit_price(offer() | {"pricing": {"currency": "EUR", "price": 50}})

    def test_no_source_shelf_proof(self):
        got = interpret_unit_price(offer())
        self.assertEqual(got["source_status"], "source_only")
        self.assertFalse(got["checkout_verified"])
        self.assertFalse(got["rank_eligible"])


if __name__ == "__main__":
    unittest.main()
