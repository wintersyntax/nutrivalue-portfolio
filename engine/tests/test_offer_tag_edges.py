"""Adversarial future offers, independent of the private 863-row census."""
import unittest
from nutrivalue.offer_categories import classify_offer


class TagEdgeTests(unittest.TestCase):
    def test_explicit_lists_and_bundles_never_gain_a_joint_denominator(self):
        for title in ('Laks + torsk', 'Coca Cola + Pepsi', 'Granola/Müsli',
                      'Grønnsaker + tunfisk', 'Kjeks og melk'):
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertIn(result.offer_kind, ('bundle', 'assortment'))
                self.assertFalse(result.food_analysis_eligible)
                self.assertIsNone(result.food_key)

    def test_pet_food_cannot_hide_independent_food_components(self):
        for title, tags in {'Hundemat + Coca Cola': ('drink', 'non_food'),
                            'Kattemat/Kjeks': ('non_food', 'sweets')}.items():
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.category_tags, tags)
                self.assertFalse(result.food_analysis_eligible)

    def test_storage_cue_applies_to_sold_food_not_ingredient(self):
        for title, tags in {
            'Yoghurt med frosne blåbær': ('dairy',),
            'Gryte med frosne grønnsaker': ('prepared',),
            'Smoothiemiks med frosne jordbær': ('produce',),
            'Grønnsaker med frosne erter': ('produce',),
            'Frosne torskefileter/Kjøttdeig': ('fish', 'frozen', 'meat'),
            'Drikkeyoghurt/Kjeks': ('dairy', 'drink', 'sweets'),
        }.items():
            with self.subTest(title=title):
                self.assertEqual(classify_offer(title).category_tags, tags)

    def test_food_flavour_never_overrides_explicit_nonfood_head(self):
        for title in ('Sjokolade håndsåpe', 'Kaffe håndsåpe', 'Honning håndsåpe',
                      'Sjokolade hudkrem'):
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.category_tags, ('non_food',))
                self.assertFalse(result.food_analysis_eligible)

    def test_flavour_context_survives_slashes_and_unmarked_ingredients(self):
        for title, tags in {
            'Drikkeyoghurt sjokolade': ('dairy', 'drink'),
            'Yoghurt sjokolade/vanilje': ('dairy',),
            'Yoghurt jordbær/vanilje/blåbær': ('dairy',),
            'Chips salt/paprika': ('snacks',),
            'Ritz kjeks og Tuc kjeks salt/paprika': ('snacks',),
        }.items():
            with self.subTest(title=title):
                self.assertEqual(classify_offer(title).category_tags, tags)


if __name__ == '__main__':
    unittest.main()
