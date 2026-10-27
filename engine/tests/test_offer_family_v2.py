"""TASK-041 v2: original synthetic product-family regression set.

Cases represent error classes seen while reviewing 863 private saved source
headlines across nine chains, not a copied/private offer table, GTIN, price
assertion or independently verified shop inventory.
"""
from __future__ import annotations

import unittest

from nutrivalue.offer_categories import (
    CATEGORIES, PRODUCT_FAMILIES, TAXONOMY_VERSION, classify_offer,
)


class ProductFamilyV2Tests(unittest.TestCase):
    def test_version_and_product_families_use_reviewed_broad_tags_only(self):
        self.assertEqual(TAXONOMY_VERSION, 6)
        self.assertIn("snacks", CATEGORIES)
        self.assertIn("sweets", CATEGORIES)
        self.assertTrue(all(category in CATEGORIES for category, _, _ in PRODUCT_FAMILIES))
        # Broad product-family improvements must not erase previously
        # reviewed type anchors needed later for evidence-gated nutrition.
        for heading, key in (
            ("SKYR NATURELL", "skyr"),
            ("COTTAGE CHEESE", "cottage_cheese"),
            ("LAKSEFILET", "salmon"),
            ("KARBONADEDEIG", "lean_beef_mince"),
        ):
            with self.subTest(heading=heading):
                self.assertEqual(classify_offer(heading).food_key, key)

    def test_heading_primary_product_family_wins_over_flavor_ingredients(self):
        samples = {
            "OSTEPOP CHEDDAR CHILI": "snacks",
            "CHEDDAR OST": "dairy",
            "PRINGLES PAPRIKA": "snacks",
            "POTETGULL PAPRIKA": "snacks",
            "SALTE KJEKS": "snacks",
            "COCA-COLA UTEN SUKKER": "drink",
            "ENERGIDRIKK MANGO": "drink",
            "BRINGEBÆR-SMOOTHIE": "drink",
            "FRUKTDRIKK JORDBÆR": "drink",
            "BLACK TEA PEACH & MANGO": "drink",
            "KAFFE": "drink",
            "DRIKKEYOGHURT JORDBÆR": "dairy",
            "SJOKOMELK JORDBÆR": "dairy",
            "KVARG": "dairy",
            "PHILADELPHIA HVITLØK": "dairy",
            "MELKESJOKOLADE": "sweets",
            "SMÅGODT I LØSVEKT": "sweets",
            "SALAMI": "meat",
            "KRYDDERSALAMI": "meat",
            "LEVERPOSTEI": "meat",
            "LAMB FÅRIKÅLKJØTT": "meat",
            "KYLLINGKJØTTDEIG": "meat",
            "NORSKE PLOMMER": "produce",
            "CRISPISALAT": "produce",
            "RØD CHILI": "produce",
            "GRANATEPLE": "produce",
            "TAGLIATELLE": "grain",
            "FULLKORNPASTA": "grain",
            "GRANOLA": "grain",
            "CROISSANT MED SMØR": "grain",
            "POLARBRØD": "grain",
            "SØRLANDSIS": "frozen",
            "SØRLANDSIS SJOKOLADE": "frozen",
            "SENSODYNE CLINICAL WHITE": "non_food",
            "GRØNNSÅPE": "non_food",
            "SALMIAKK 750 ML": "non_food",
            "FISH & CRISP": "fish",
            "FISKEBURGER MED PAPRIKA": "fish",
            "POKE BOWL LAKS": "prepared",
            "KYLLINGGRYTE": "prepared",
            "VÅRRULLER": "prepared",
            "MOZZARELLA STICKS": "prepared",
            "PASTASAUS MED LAKS": "pantry",
            "CHEDDAR DIPP": "pantry",
            "HONNING": "pantry",
            "BASILIKUM PESTO": "pantry",
            "SHEBA TUNFISK": "non_food",
            "DREAMIES LAKS OG KYLLING": "non_food",
            "PEDIGREE DYREMAT": "non_food",
            "ROSEBUKETT": "non_food",
            "TULIPANER": "non_food",
            "HÅNDSÅPE": "non_food",
            "LISTERINE": "non_food",
            "LIBRESSE": "non_food",
            "OMOKAPSLER OMO": "non_food",
        }
        for headline, category in samples.items():
            with self.subTest(headline=headline):
                decision = classify_offer(headline)
                self.assertEqual(decision.food_category, category)
                self.assertNotEqual(decision.status, "unmapped")
                self.assertIsNone(decision.food_key) if category in {
                    "snacks", "sweets", "prepared", "non_food"
                } else None

    def test_form_processed_products_are_never_approved_as_raw_sku(self):
        review_only = {
            "FISKEGRATENG": "fish",
            "FISKEPINNER": "fish",
            "FISKEBURGER PAPRIKA": "fish",
            "SPRØBAKT TORSK": "fish",
            "KYLLINGFILET SPISEKLAR": "meat",
            "RØKT SVINEKAM": "meat",
        }
        for title, cat in review_only.items():
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.status, "needs_review")
                self.assertEqual(result.food_category, cat)
                self.assertEqual(
                    result.food_key,
                    {
                        "FISKEPINNER": "fish_fingers",
                        "FISKEBURGER PAPRIKA": "fish_burger",
                    }.get(title),
                )

        for title in ("LAMMEBOG", "KYLLINGKJØTTDEIG", "SVINEKNOKE"):
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.food_category, "meat")
                self.assertEqual(result.form, "unspecified")

        for title in ("SALAMI", "MORTADELLA", "PANCETTA"):
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.food_category, "meat")
                self.assertEqual(result.form, "processed")
                self.assertEqual(
                    result.food_key,
                    "salami" if title == "SALAMI" else None,
                )

    def test_ambiguous_brand_selection_does_not_infer_sku(self):
        for title in ("UKENS TILBUD", "PAKKEDEAL", "ET UTVALG DIVERSE VARER"):
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.status, "unmapped")
                self.assertIsNone(result.food_key)
        for title in ("BIG ONE PIZZA", "TORO FERDIGRETTER"):
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertIsNone(result.food_key)

    def test_description_only_does_not_override_clear_title_or_infer_item(self):
        drink = classify_offer("COCA-COLA U/SUKKER",
                               "Stk-pris. Serveres med snacks og frukt.")
        self.assertEqual(drink.food_category, "drink")
        self.assertIsNone(drink.food_key)
        cat = classify_offer("DAGENS TILBUD",
                             "Laks og melk, ulike produkter")
        self.assertEqual(cat.status, "needs_review")
        self.assertIsNone(cat.food_key)


if __name__ == "__main__":
    unittest.main()
