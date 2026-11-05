"""Fictional offline regression examples."""
import json
import unittest
from pathlib import Path

from nutrivalue.offer_categories import TAXONOMY_VERSION, classify_offer
from nutrivalue.page_promotions import validate_output, scope_containment


def promotion(kind, scope, n=None, m=None, percent=None, member=False):
    return {
        "kind": kind, "scope": scope,
        "n": n, "m": m, "percent": percent,
        "amount_nok": None, "member_only": member,
    }


class PublicReleaseTests(unittest.TestCase):
    def test_taxonomy_and_catalog(self):
        self.assertEqual(TAXONOMY_VERSION, 6)
        path = Path(__file__).resolve().parents[1] / "data/reference/food_roles.json"
        catalog = json.loads(path.read_text())
        self.assertEqual(len(catalog["foods"]), 140)

    def test_processed_food_and_raw_food_safety(self):
        self.assertEqual(
            classify_offer("Kokt skinke").food_key, "cooked_ham"
        )
        self.assertIsNone(
            classify_offer("Kyllingfilet", "110 g, naturell", 110).food_key
        )
        self.assertIsNone(
            classify_offer("Kyllingfilet", "500 g, grillet").food_key
        )

    def test_equivalent_percent_is_not_double_counted(self):
        badge = promotion(
            "n_for_m", "TEST AKCIJA: SVI FIKTIVNI ARTIKLI",
            n=3, m=2
        )
        restatement = promotion(
            "percent_off",
            "SVI FIKTIVNI ARTIKLI, TREKK FRA I KASSEN",
            percent=33
        )
        self.assertGreaterEqual(
            scope_containment(badge["scope"], restatement["scope"]),
            0.5
        )
        parsed = validate_output({
            "page_class": "promotion",
            "promotions": [badge, restatement],
        })
        self.assertEqual(
            [p["kind"] for p in parsed["promotions"]],
            ["n_for_m"]
        )

    def test_independent_discount_remains(self):
        badge = promotion("n_for_m", "FIKTIVNE VARER", n=3, m=2)
        other = promotion("percent_off", "KAFFE SAMO DANAS", percent=33)
        parsed = validate_output({
            "page_class": "promotion",
            "promotions": [badge, other],
        })
        self.assertEqual(len(parsed["promotions"]), 2)


if __name__ == "__main__":
    unittest.main()
