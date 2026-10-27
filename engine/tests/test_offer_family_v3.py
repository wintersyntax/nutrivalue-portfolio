"""Synthetic category regressions, including unseen compounds and conflicts."""
import unittest
from nutrivalue.offer_categories import classify_offer


class ProductKindTests(unittest.TestCase):
    def test_product_head_wins_over_flavour_and_ingredient(self):
        for title, category in {
            'Fløteis med sjokolade': 'frozen',
            'Sørlandsis sjokolade og vanilje': 'frozen',
            'Sjokolademelk vanilje': 'dairy',
            'Kaffedrikk sjokolade': 'drink',
            'Potetmos gressløk og melk': 'pantry',
            'Klar kjøttbuljong': 'pantry',
            'Kylling og grønnsaksbuljong': 'pantry',
            'Tunfisk i olivenolje og pepper': 'fish',
            'Tunfisk fransk dressing': 'fish',
            'Kjøttboller i tomatsaus': 'meat',
            'Danonino drikke jordbær': 'dairy',
            'Gresk yoghurt vanilje og honning': 'dairy',
            'Japp, gullbrød og sjokolade': 'sweets',
            'Hakkede tomater med hvitløk': 'pantry',
            'Tomater hakkede/hele/passata': 'pantry',
            'Fårikål med poteter': 'prepared',
            'Viltgryte/bacalao': 'prepared',
            'Karbonader/stekte kaker': 'meat',
            'Pai med skinke og brokkoli': 'prepared',
            'Fersk spekeskinke': 'meat',
            'Gourmetmajones': 'pantry',
            'Smoothiemiks mango': 'produce',
        }.items():
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.food_category, category)
                if category in {'frozen','pantry','prepared'}:
                    self.assertIsNone(result.food_key)

    def test_new_compounds_and_inflections_generalize(self):
        for title, cat in {
            'Fjellgårdens delikatessepoteter': 'produce',
            'Nyprodusert familiebrød': 'grain',
            'Urtesauser': 'pantry',
            'Havrekjeks': 'sweets',
            'Marsipankake': 'sweets',
            'Vaffelkjeks': 'sweets',
            'Vårull': 'prepared',
            'Fryste burgere': 'meat',
            'Pølse': 'meat',
            'Rein medaljong': 'meat',
            'Saltkjøtt': 'meat',
            'Grønnsaker': 'produce',
            'Høstlig pilefleecegenser': 'non_food',
            'Arbeidssokk': 'non_food',
            'Grønnsåpe': 'non_food',
            'Opphøgde potteter': 'frozen',
        }.items():
            with self.subTest(title=title):
                self.assertEqual(classify_offer(title).food_category, cat)

    def test_mixed_promotions_are_not_silently_one_category(self):
        for title, description in (
            ('Granola/Kesam mager', None),
            ('Müslibar/Froosh/Litago', None),
            ('Pakke', 'Coca Cola + pizzabolle'),
            ('Kjeks og tannkrem', None),
            ('Laks og såpe', None),
        ):
            with self.subTest(title=title):
                result = classify_offer(title, description)
                self.assertEqual(result.status, 'needs_review')
                self.assertIsNone(result.food_category)
                self.assertIsNone(result.food_key)
                self.assertFalse(result.food_analysis_eligible)

    def test_generic_brands_and_marketing_need_evidence(self):
        for title in ('SARITAS', 'PÅLEGG', 'Frokost', 'Crispies', 'Biscoff',
                      'MER PÆRE', 'Dagens diverse'):
            with self.subTest(title=title):
                self.assertIsNone(classify_offer(title).food_category)

    def test_primary_food_type_not_overridden_by_serving_copy(self):
        result = classify_offer('Gourmetmajones', 'Server med egg, laks og brød')
        self.assertEqual(result.food_category, 'pantry')
        result = classify_offer('Spekeskinke', 'Perfekt på pizza')
        self.assertEqual(result.food_category, 'meat')

    def test_nonfood_and_uncertain_categories_are_ineligible(self):
        for title, description in (
            ('SENSODYNE CLINICAL WHITE', None), ('Kattunfisk kattemat', None),
            ('Ny vaskesåpe', None), ('Familiepakking', 'Laks og melk'),
            ('Salm i akk', None), ('Ukens ukjente', None),
        ):
            with self.subTest(title=title):
                self.assertFalse(classify_offer(title, description).food_analysis_eligible)
        for title in ('Laksefilet', 'Røkt laks', 'Epler'):
            self.assertTrue(classify_offer(title).food_analysis_eligible)

    def test_ambiguous_cleaning_word_requires_liquid_basis(self):
        self.assertEqual(classify_offer('Salmiakk', '750 ml rengjøring').food_category,
                         'non_food')
        self.assertIsNone(classify_offer('Salmiakk').food_category)
        self.assertEqual(classify_offer('Salmiakkpastiller').food_category, 'sweets')
        self.assertEqual(classify_offer('Fryste burgere').form, 'frozen')

    def test_hybrid_names_and_filled_wraps_do_not_become_raw_meat_or_bread(self):
        self.assertIsNone(classify_offer('Donut burger').food_category)
        result = classify_offer('Butikkens wraps', 'Tacowrap kjøtt/fajitas kylling')
        self.assertEqual(result.food_category, 'prepared')
        self.assertIsNone(result.food_key)
        self.assertIsNone(classify_offer('Et utvalg drikke', 'Melk/juice').food_category)

    def test_unknown_brand_with_fruit_flavours_is_not_raw_produce(self):
        result = classify_offer('Dessertmerket', 'Fra frysedisken. Bringebær/jordbær')
        self.assertIsNone(result.food_category)
        self.assertFalse(result.food_analysis_eligible)
        result = classify_offer('UkjentBar/Fruktdrikk/Litago')
        self.assertIsNone(result.food_category)

    def test_sold_product_before_med_wins_over_added_ingredient(self):
        for title, category in {
            'Yoghurt med kjeks': 'dairy',
            'Gryte med grønnsaker': 'prepared',
            'Nuggets med grønnsaker': 'prepared',
            'Kyllingpølse med majones': 'meat',
            'Yoghurt med iskrem': 'dairy',
        }.items():
            with self.subTest(title=title):
                self.assertEqual(classify_offer(title).food_category, category)

    def test_food_species_never_overrides_feed_or_cosmetics(self):
        for title, description in (
            ('Tunfisk fiskefôr', None), ('Tunfisk dyrefôr', None),
            ('Pølse dyrefôr', None), ('Mer tannkrem', '250 ml'),
            ('Mer grønnsåpe', '1 l'),
        ):
            with self.subTest(title=title):
                result = classify_offer(title, description)
                self.assertEqual(result.food_category, 'non_food')
                self.assertFalse(result.food_analysis_eligible)

    def test_slash_lists_include_raw_foods_but_flavours_stay_with_product(self):
        for title in ('Kjeks/Melk', 'Kjeks/epler', 'Grønnsaker/tunfisk'):
            with self.subTest(title=title):
                self.assertIsNone(classify_offer(title).food_category)
        self.assertEqual(classify_offer('Yoghurt jordbær/bringebær').food_category,
                         'dairy')

    def test_compound_food_heads_do_not_turn_fish_and_meat_into_cakes(self):
        for title, category in {'Fiskekake': 'fish', 'Kjøttkake': 'meat',
                                'Kjøttbrød': 'meat'}.items():
            with self.subTest(title=title):
                self.assertEqual(classify_offer(title).food_category, category)
        for title in ('Pasta med grønnsaker', 'Ris med grønnsaker', 'Melk/vann'):
            with self.subTest(title=title):
                self.assertIsNone(classify_offer(title).food_category)

    def test_safe_boundaries_and_imitation_remain_conservative(self):
        for title in ('Lakseolje', 'MАКRELL', 'Brødkniv', 'Såpedispenser'):
            self.assertFalse(classify_offer(title).food_analysis_eligible)
        self.assertIsNone(classify_offer('Vegansk burger').food_key)
        self.assertIsNone(classify_offer('Vegansk burger').food_category)
        self.assertIsNone(classify_offer('Tunfisk i olivenolje').food_key)


if __name__ == '__main__':
    unittest.main()
