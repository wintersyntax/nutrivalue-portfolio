"""Synthetic-only regression tests for offline candidate-evidence checks."""
from __future__ import annotations

import unittest
from decimal import Decimal

from nutrivalue.product_candidates import (
    assess, grams, kassalapp_candidates, name_grams, ngdata_candidates,
    nutrition_flags, offer_brand, offer_grams, reference_flags, score, search_term,
)

OFFER = {
    "heading": "Kyllingfilet", "description": "500 g, DemoBrand",
    "quantity": "500", "unit": "g", "pack_count": 1,
    "price_ore": 6990, "food_key": "chicken_breast",
}
# SYN- IDs are deliberately not valid retail EANs/GTINs.
NG = {"hits": {"hits": [{"_source": {
    "ean": "SYN-0001", "title": "Kyllingfilet", "subtitle": "500g",
    "brand": "DemoBrand", "measurementValue": 500, "measurementType": "g",
    "pricePerUnit": "69.90",
    "nutritionalContent": [
        {"name": "energi_kcal", "amount": 110, "unit": "kcal"},
        {"name": "energi_kj", "amount": 460, "unit": "kJ"},
        {"name": "protein", "amount": 23, "unit": "g"},
    ],
}}]}}
KL = {"data": [{
    "ean": "SYN-0001", "name": "DemoBrand Kyllingfilet 500g",
    "brand": "DemoBrand", "weight": 500, "weight_unit": "g",
    "current_price": "70", "store": {"name": "Example Shop"},
    "nutrition": [
        {"code": "energi_kcal", "amount": 110, "unit": "kcal"},
        {"code": "energi_kj", "amount": 460, "unit": "kJ"},
        {"code": "protein", "amount": 23, "unit": "g"},
    ],
}]}


class OfflineCandidates(unittest.TestCase):
    def test_pack_parsing(self):
        self.assertEqual(grams("0.5", "kg"), Decimal("500"))
        self.assertEqual(name_grams("Example 2x110g"), Decimal("220"))
        self.assertEqual(offer_grams(dict(OFFER, quantity=110, pack_count=2)),
                         Decimal("220"))
        self.assertIsNone(grams("1", "litre"))

    def test_brand_and_term(self):
        self.assertEqual(offer_brand(OFFER["description"]), "DemoBrand")
        self.assertEqual(search_term(OFFER), "kyllingfilet demobrand")

    def test_offline_provider_payload_normalization(self):
        ng, kl = ngdata_candidates(NG), kassalapp_candidates(KL)
        self.assertEqual((ng[0]["ean"], ng[0]["grams"],
                          ng[0]["nutrition"]["protein_g"]),
                         ("SYN-0001", Decimal("500"), Decimal("23")))
        self.assertEqual((kl[0]["ean"], kl[0]["grams"]),
                         ("SYN-0001", Decimal("500")))
        self.assertEqual(len(ng + kl), 2)

    def test_candidate_ranking_not_verification(self):
        ng, kl = ngdata_candidates(NG), kassalapp_candidates(KL)
        self.assertEqual(score(OFFER, ng[0]), (7, [
            "same pack", "brand", "name", "same price"]))
        ranked = assess(OFFER, ng + kl)
        self.assertEqual(ranked["status"], "likely, same EAN from both sources")
        self.assertEqual(assess(OFFER, [])["status"], "no candidate")

    def test_mismatch_prevents_likely_status(self):
        candidate = ngdata_candidates(NG)[0]
        candidate["grams"] = Decimal("250")
        self.assertIn("pack mismatch", score(OFFER, candidate)[1])
        self.assertEqual(assess(OFFER, [candidate])["status"], "possible")

    def test_nutrition_flags_and_reference_review(self):
        candidate = ngdata_candidates(NG)[0]
        flags = nutrition_flags(candidate["nutrition"])
        self.assertEqual([f for f in flags if not f.startswith("basis ")], [])
        self.assertIn("kcal field holds the kJ value", nutrition_flags({
            "kcal_field": Decimal("460"), "kj": Decimal("460"),
            "protein_g": Decimal("23"),
        }))
        self.assertIn("no protein value", nutrition_flags({}))
        reference = {"protein_g": Decimal("23"), "kcal": Decimal("110")}
        self.assertEqual(reference_flags(candidate["nutrition"], reference), [])
        candidate["nutrition"]["protein_g"] = Decimal("3.5")
        assessed = assess(OFFER, [candidate], reference)
        self.assertEqual(assessed["status"], "check nutrition")
        self.assertIn("may be misclassified", assessed["classification_check"])


if __name__ == "__main__":
    unittest.main()
