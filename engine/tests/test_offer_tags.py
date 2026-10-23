"""Synthetic multi-tag search contracts; not a nutrition approval mechanism."""
import unittest
from nutrivalue.offer_categories import classify_offer


class OfferTagTests(unittest.TestCase):
    def test_single_product_compatible_view_has_one_supported_category(self):
        result = classify_offer('Laksefilet')
        self.assertEqual(result.food_category, 'fish')
        self.assertEqual(result.category_tags, ('fish',))
        self.assertEqual(result.offer_kind, 'single_product')
        self.assertEqual(result.tag_status, 'complete')

    def test_separate_product_selection_has_multiple_supported_tags(self):
        for title, tags in {
            'Granola/Kesam mager': ('dairy', 'grain'),
            'Kjeks/Melk': ('dairy', 'drink', 'sweets'),
            'Grønnsaker/tunfisk': ('fish', 'produce'),
            'Sjokolade og pretzel': ('snacks', 'sweets'),
        }.items():
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.category_tags, tags)
                self.assertEqual(result.offer_kind, 'assortment')
                self.assertEqual(result.tag_status, 'complete')
                self.assertIsNone(result.food_category)
                self.assertIsNone(result.food_key)
                self.assertFalse(result.food_analysis_eligible)

    def test_bundle_tags_describe_components_without_approving_joint_rate(self):
        result = classify_offer('Pakke', 'Coca Cola + pizzabolle')
        self.assertEqual(result.category_tags, ('drink', 'prepared'))
        self.assertEqual(result.offer_kind, 'bundle')
        self.assertFalse(result.food_analysis_eligible)

    def test_ingredients_and_nonfood_are_not_additional_product_tags(self):
        for title, tags in {
            'Yoghurt med kjeks': ('dairy',),
            'Yoghurt med melk': ('dairy',),
            'Pizza med laks og ost': ('prepared',),
            'Fløteis sjokolade': ('frozen',),
            'Kattemat med tunfisk': ('non_food',),
        }.items():
            with self.subTest(title=title):
                self.assertEqual(classify_offer(title).category_tags, tags)

    def test_unknown_names_and_partial_selections_do_not_gain_fake_tags(self):
        unknown = classify_offer('Ukjent merke')
        self.assertEqual(unknown.category_tags, ())
        self.assertEqual(unknown.tag_status, 'unresolved')
        partial = classify_offer('UkjentBar/UkjentDrikk/Litago')
        self.assertEqual(partial.category_tags, ('dairy', 'drink'))
        self.assertEqual(partial.tag_status, 'partial')
        self.assertFalse(partial.food_analysis_eligible)

    def test_single_product_can_have_overlapping_category_tags(self):
        frozen = classify_offer('Frosne torskefileter')
        self.assertEqual(frozen.food_category, 'fish')
        self.assertEqual(frozen.category_tags, ('fish', 'frozen'))
        self.assertEqual(frozen.offer_kind, 'single_product')
        processed = classify_offer('Frosne fiskekaker')
        self.assertEqual(processed.category_tags, ('fish', 'frozen'))
        self.assertEqual(processed.form, 'processed')
        beverage = classify_offer('Drikkeyoghurt jordbær')
        self.assertEqual(beverage.food_category, 'dairy')
        self.assertEqual(beverage.category_tags, ('dairy', 'drink'))
        self.assertEqual(beverage.offer_kind, 'single_product')

    def test_only_category_evidence_is_not_nutrition_evidence(self):
        result = classify_offer('Røkt laks')
        self.assertEqual(result.category_tags, ('fish',))
        self.assertIsNone(result.food_key)
        self.assertEqual(result.status, 'needs_review')
        hinted = classify_offer('Dagens tilbud', 'Torsk')
        self.assertEqual(hinted.tag_status, 'candidate')
        self.assertFalse(hinted.food_analysis_eligible)


if __name__ == '__main__':
    unittest.main()
