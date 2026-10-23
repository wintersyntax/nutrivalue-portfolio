"""TASK-033: strictly aggregate, synthetic Fra-vs-API quantity review tests."""
from __future__ import annotations

import unittest

from scripts.audit_tjek_offer_conditions import profile_minimum_pack_evidence


def example(description="Fra 80 g pr pk. Pr kg 250,00",
            size=80, unit="g", price=20):
    basis = "kg" if unit in {"g", "kg", "hg"} else "l"
    factor = {"g": 0.001, "kg": 1, "hg": 0.1,
              "l": 1, "cl": 0.01, "ml": 0.001}[unit]
    return {
        "id": "test-private-id", "heading": "Test brand",
        "description": description,
        "pricing": {"currency": "NOK", "price": price, "pre_price": None},
        "quantity": {
            "unit": {"symbol": unit, "si": {"symbol": basis, "factor": factor}},
            "size": {"from": size, "to": size},
            "pieces": {"from": 1, "to": 1}
        },
    }


class MinimumPackProfileTests(unittest.TestCase):
    def test_one_floor_equal_and_pack_label(self):
        counts = profile_minimum_pack_evidence([example()])
        self.assertEqual(counts["guarded_offers"], 1)
        self.assertEqual(counts["single_floor_cue"], 1)
        self.assertEqual(counts["floor_equals_source_size"], 1)
        self.assertEqual(counts["floor_next_to_pack_label"], 1)

    def test_explicit_two_for_and_floor_is_counted_but_not_accepted(self):
        counts = profile_minimum_pack_evidence([
            example("2 for 40. Fra 80 g pr pk", price=40)])
        self.assertEqual(counts["api_explicit_multibuy_and_floor"], 1)
        self.assertEqual(counts["guarded_offers"], 1)
        self.assertNotIn("kg_rate", str(counts))
        self.assertNotIn("test-private-id", str(counts))

    def test_floor_less_than_source_size(self):
        counts = profile_minimum_pack_evidence([
            example("Fra 80 g pr pk", size=100)])
        self.assertEqual(counts["floor_less_than_source_size"], 1)

    def test_floor_greater_than_source_size(self):
        counts = profile_minimum_pack_evidence([
            example("Fra 100 g pr pk", size=80)])
        self.assertEqual(counts["floor_greater_than_source_size"], 1)

    def test_multiple_floors_marked_without_unique_size(self):
        counts = profile_minimum_pack_evidence([
            example("Fra 80 g og fra 90 g pr pk", size=80)])
        self.assertEqual(counts["multiple_floor_cues"], 1)
        self.assertEqual(counts["floor_vs_source_size_multiple_floors"], 1)

    def test_unrelated_dimension_and_date_not_counted(self):
        counts = profile_minimum_pack_evidence([
            example("Fra 1 l. Pr kg 250,00", size=80),
            example("Fra 7. august, Pr kg 250,00", size=80),
        ])
        self.assertEqual(counts, {})

    def test_fractional_floor_and_si_conversion(self):
        counts = profile_minimum_pack_evidence([
            example("Fra 0,08 kg pr pk", size=80, unit="g")])
        self.assertEqual(counts["floor_equals_source_size"], 1)

    def test_aggregate_does_not_print_money_or_product_titles(self):
        counts = profile_minimum_pack_evidence([
            example("Fra 80 g pr pk. PR KG 999,00", size=80, price=79.92)])
        self.assertNotIn("999", str(counts))
        self.assertNotIn("79.92", str(counts))
        self.assertNotIn("Test brand", str(counts))


if __name__ == "__main__":
    unittest.main()
