"""TASK-033: source-only aggregate price-shape profile, synthetic fixtures only."""
from __future__ import annotations

import unittest

from nutrivalue.providers.tjek_offer_conditions import interpret_offer
from scripts.audit_tjek_offer_conditions import profile_price_fields, schema_field_counts


def specimen(description: str, price, prior, heading="Synthetic item"):
    return {
        "heading": heading, "description": description,
        "pricing": {"price": price, "pre_price": prior, "currency": "NOK"},
    }


class ConditionProfileTests(unittest.TestCase):
    def test_missing_price_and_reused_percent_description_are_counted(self):
        samples = [
            specimen("25 % rabatt på alle varer", None, None),
            specimen("25   % rabatt på alle varer", None, None),
            specimen("25% rabatt på alle varer", 50, 70),
            specimen("Normalpris", 20, 10),
        ]
        results = [interpret_offer(value) for value in samples]
        result = profile_price_fields(samples, results)
        self.assertEqual(result["price_present"], 2)
        self.assertEqual(result["pre_price_present"], 2)
        self.assertEqual(result["both_price_fields"], 2)
        self.assertEqual(result["pre_price_above_source"], 1)
        self.assertEqual(result["pre_price_below_source"], 1)
        self.assertEqual(result["percentage_matches_unverified"], 3)
        self.assertEqual(result["percent_without_source_price"], 2)
        self.assertEqual(result["percent_with_source_price"], 1)
        self.assertEqual(result["percent_with_pre_price"], 1)
        self.assertEqual(result["percent_without_both_prices"], 2)
        self.assertEqual(result["percent_literal_in_description"], 3)
        self.assertEqual(result["distinct_percent_descriptions"], 2)
        self.assertEqual(result["max_same_percent_description"], 2)
        self.assertFalse(any("item" in key or "amount" in key
                             for key in result))

    def test_heading_only_percentage_and_no_raw_text_output(self):
        value = specimen("", None, None, heading="20 % rabatt på alle varer")
        result = profile_price_fields([value], [interpret_offer(value)])
        self.assertEqual(result["percent_literal_in_heading"], 1)
        self.assertNotIn("percent_literal_in_description", result)
        self.assertEqual(result["distinct_percent_descriptions"], 0)
        self.assertEqual(result["max_same_percent_description"], 0)
        self.assertNotIn("20 %", repr(result))

    def test_non_discount_percentage_not_treated_as_discount(self):
        value = specimen("100 % ull", 199, 249)
        result = profile_price_fields([value], [interpret_offer(value)])
        self.assertNotIn("percentage_matches_unverified", result)
        self.assertEqual(result["price_present"], 1)
        self.assertEqual(result["pre_price_above_source"], 1)

    def test_schema_inventory_reports_only_field_names_and_occurrence_counts(self):
        samples = [
            specimen("25 % rabatt", 10, None),
            specimen("25 % rabatt", 20, 30),
        ]
        samples[0]["pricing"]["discount_hint"] = {"private": 123}
        samples[0]["price_kind"] = "private source text"
        got = schema_field_counts(samples)
        self.assertEqual(got["pricing_keys"]["currency"], 2)
        self.assertEqual(got["pricing_keys"]["discount_hint"], 1)
        self.assertEqual(got["offer_keys"]["price_kind"], 1)
        self.assertNotIn("private source text", str(got))
        self.assertNotIn("123", str(got))
        self.assertNotIn("discount", str(got["offer_keys"]))

    def test_mismatched_offer_counts_raise(self):
        with self.assertRaises(ValueError):
            profile_price_fields([specimen("", 10, None)], [])


if __name__ == "__main__":
    unittest.main()
