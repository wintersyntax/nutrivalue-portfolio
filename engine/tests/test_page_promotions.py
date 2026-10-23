"""TASK-053: synthetic contract, scoring, OCR cross-check and page selection."""
from __future__ import annotations

import json
import unittest

from nutrivalue.page_promotions import (
    PromotionOutputError, numbers_supported, ocr_lines, ocr_status,
    score_page, validate_output,
)


def promo(kind, scope="Synthetic group", member=False, n=None, m=None, percent=None,
          amount=None, optional=False):
    return {"kind": kind, "n": n, "m": m, "percent": percent, "amount_nok": amount,
            "member_only": member, "scope": scope, "optional": optional}


def answer(page_class, *items):
    return {"page_class": page_class,
            "promotions": [{k: v for k, v in i.items() if k != "optional"} for i in items]}


class ContractTests(unittest.TestCase):
    def test_valid_output_and_code_fence(self):
        raw = "```json\n" + json.dumps(answer("promotion", promo("n_for_m", n=3, m=2))) + "\n```"
        self.assertEqual(validate_output(raw)["promotions"][0]["n"], 3)

    def test_kind_specific_numbers_are_enforced(self):
        bad = [promo("n_for_m", n=2, m=3), promo("n_for_m", n=3, m=2, percent=10),
               promo("percent_off", percent=0), promo("percent_off", percent=150),
               promo("amount_off", amount=None), promo("bonus_percent", percent=20, n=1)]
        for item in bad:   # TASK-056: a bad item is dropped, not the whole page
            parsed = validate_output(answer("promotion", item))
            self.assertEqual((parsed["promotions"], len(parsed["item_errors"])), ([], 1))

    def test_structure_errors(self):
        for raw in ("not json", {"page_class": "promotion"},
                    {"page_class": "x", "promotions": []},
                    {"page_class": "promotion", "promotions": {"kind": "n_for_m"}}):
            with self.assertRaises(PromotionOutputError):
                validate_output(raw)

    def test_string_booleans_and_numbers_rejected(self):
        item = promo("percent_off", percent=20)
        item["member_only"] = "true"
        self.assertEqual(validate_output(answer("promotion", item))["promotions"], [])
        item = promo("amount_off", amount="10")
        self.assertEqual(len(validate_output(answer("promotion", item))["item_errors"]), 1)


class ScoringTests(unittest.TestCase):
    REF = {"page_class": "promotion", "promotions": [
        promo("percent_off", "Libero, Pampers og Änglamark bleier", True, percent=50),
        promo("amount_off", "Coop karbonader", True, amount=10),
        promo("amount_off", "Coop pizza", True, amount=10),
        promo("bonus_percent", "Velkomstbonus", True, percent=10, optional=True)]}

    def test_exact_match_counts_true_positives_and_scope_overlap(self):
        pred = validate_output(answer("promotion",
                                      promo("percent_off", "bleier Libero Pampers", True, percent=50),
                                      promo("amount_off", "Coop pizza", True, amount=10)))
        result = score_page(self.REF, pred)
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (2, 0, 1))
        self.assertTrue(result["page_class_correct"])
        self.assertIn(1.0, result["scope_overlaps"])

    def test_member_flag_error_is_false_assertion_and_miss(self):
        pred = validate_output(answer("promotion", promo("percent_off", "bleier", False, percent=50)))
        result = score_page(self.REF, pred)
        self.assertEqual((result["tp"], result["fp"], result["member_errors"]), (0, 1, 1))

    def test_optional_items_are_neutral(self):
        pred = validate_output(answer("promotion", promo("bonus_percent", "x", True, percent=10)))
        self.assertEqual(score_page(self.REF, pred)["fp"], 0)

    def test_wrong_number_and_missing_prediction(self):
        pred = validate_output(answer("promotion", promo("percent_off", "bleier", True, percent=40)))
        self.assertEqual(score_page(self.REF, pred)["fp"], 1)
        self.assertEqual(score_page(self.REF, None)["fn"], 3)

    def test_acceptable_alternative_page_class(self):
        ref = {"page_class": "non_offer", "acceptable_page_classes": ["promotion"],
               "promotions": [promo("n_for_m", n=3, m=2, optional=True)]}
        pred = validate_output(answer("promotion", promo("n_for_m", n=3, m=2)))
        result = score_page(ref, pred)
        self.assertEqual((result["page_class_correct"], result["fp"]), (True, 0))

    def test_promotion_invented_on_non_offer_page(self):
        ref = {"page_class": "non_offer", "promotions": []}
        pred = validate_output(answer("promotion", promo("n_for_m", n=3, m=2)))
        result = score_page(ref, pred)
        self.assertEqual((result["page_class_correct"], result["fp"]), (False, 1))


def box(x0, x1, y0, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class OcrLayoutTests(unittest.TestCase):
    def test_split_badge_tokens_are_joined_and_confirmed(self):
        tokens = [("3", box(412, 660, 742, 1105)), ("FOR", box(648, 742, 818, 1010)),
                  ("2", box(723, 985, 745, 1100)), ("Laks 250 g", box(1200, 1400, 1100, 1120))]
        lines = ocr_lines(tokens)
        self.assertIn("3 for 2", lines)
        self.assertEqual(ocr_status([promo("n_for_m", n=3, m=2), promo("n_for_m", n=4, m=3)], lines),
                         ["confirmed", "absent"])

    def test_degree_sign_and_separate_percent_token(self):
        lines = ocr_lines([("-30°", box(107, 460, 1089, 1347)), ("%", box(377, 494, 1119, 1217))])
        self.assertEqual(ocr_status([promo("percent_off", percent=30)], lines), ["confirmed"])

    def test_distant_tokens_are_not_joined_and_number_only_is_reported(self):
        lines = ocr_lines([("20", box(0, 50, 0, 50)), ("%", box(900, 950, 600, 650))])
        self.assertEqual(ocr_status([promo("percent_off", percent=20)], lines), ["number_only"])

    def test_vertical_for_badge_and_printed_equivalent(self):
        three, two = promo("n_for_m", n=3, m=2), promo("n_for_m", n=2, m=1)
        self.assertEqual(ocr_status([three, two], ["382", "281"]), ["confirmed", "confirmed"])
        self.assertEqual(ocr_status([three], ["33% rabatt ved kj0p av 3"]), ["confirmed"])
        self.assertEqual(ocr_status([two], ["50 % rabatt ved kjøp av 2"]), ["confirmed"])
        self.assertNotEqual(ocr_status([three], ["302 g", "1302", "33% rabatt ved kjop av 4"]),
                            ["confirmed"])

    def test_text_lines_and_ocr_confusions(self):
        self.assertEqual(ocr_status([promo("amount_off", amount=10), promo("n_for_m", n=3, m=2)],
                                    ["spar -10kr", "3t02"]), ["confirmed", "confirmed"])


class OcrAndPagesTests(unittest.TestCase):
    def test_numbers_must_appear_in_ocr_text(self):
        items = [promo("n_for_m", n=3, m=2), promo("percent_off", percent=20),
                 promo("amount_off", amount=15)]
        self.assertEqual(numbers_supported(items, "PROTEIN-DEAL 3 for 2  -20%  SPAR -10kr"),
                         [True, True, False])
        self.assertEqual(numbers_supported([promo("percent_off", percent=20)], "1200 g"), [False])


if __name__ == "__main__":
    unittest.main()
