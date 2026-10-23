import unittest,sys,json,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from w41_title_refiner_v5 import refine, pick_title, dup_token_fix, norm, candidates_for
R=Path(__file__).parent
DATA=json.loads((R/'fixtures-v4.json').read_text(encoding='utf-8'))
OCR=json.loads((R/'ocr-lines-w41.json').read_text(encoding='utf-8'))
MAP={c['candidate_id']:c for c in DATA['candidates']}
class TestW41V5(unittest.TestCase):
    def row(self,id):return refine(MAP[id],OCR)
    def test_no_identity(self):self.assertEqual(len(DATA['candidates']),104)
    def test_all_preserve_canonical_price(self):
        for c in DATA['candidates']:
            n=refine(c,OCR);self.assertEqual(n.get('printed_current_nok'),c.get('printed_current_nok'))
    def test_all_review_only(self):
        for c in DATA['candidates']:
            n=refine(c,OCR);self.assertFalse(n.get('publishable',False));self.assertTrue(n['title_decision_review_only_v5'])
    def test_paddle_remains_unaccepted(self):
        for k in ('W41-P02-N001','W41-P16-N001'):
            n=self.row(k);self.assertIsNone(n['printed_current_nok']);self.assertFalse(n['price_accepted_v4'])
    def test_genuine_doubled_couscous(self):self.assertEqual(dup_token_fix('COUSCOUS'),'COUSCOUS')
    def test_duplicate_prefix(self):self.assertEqual(dup_token_fix('RUSTICA PEPPEPPERONI'),'RUSTICA PEPPERONI')
    def test_duplicate_suffix(self):self.assertEqual(dup_token_fix('HAVREBRØDBRØD'),'HAVREBRØD')
    def test_p03_filet(self):self.assertEqual(self.row('W41-P03-C003')['proposed_product_title_v5'],'MARINERT YTREFILET AV SVIN')
    def test_p05_group(self):self.assertIn('TORTIGLIONI',self.row('W41-P05-C004')['proposed_product_title_v5'])
    def test_p06_meatballs(self):self.assertIn('TOMATSAUS',self.row('W41-P06-C001')['proposed_product_title_v5'])
    def test_p15_flour(self):self.assertEqual(norm(self.row('W41-P15-C002')['proposed_product_title_v5']),'HVETEMEL2KG')
    def test_p16_sugar(self):self.assertEqual(self.row('W41-P16-C004')['proposed_product_title_v5'],'SUKKER')
    def test_p16_milk(self):self.assertEqual(norm(self.row('W41-P16-C005')['proposed_product_title_v5']),'TINELETTMELK1')
    def test_p17_bread(self):self.assertTrue(self.row('W41-P17-C002')['proposed_product_title_v5'].startswith('GROVT & GODT'))
    def test_p20_icecream(self):self.assertIn('LITER',self.row('W41-P20-C001')['proposed_product_title_v5'])
    def test_p22_group(self):self.assertEqual(self.row('W41-P22-C001')['proposed_product_title_v5'],'GIGLI/PAPPARDELLE/TORTIGLIONI')
    def test_p24_lamb(self):self.assertEqual(norm(self.row('W41-P24-C003')['proposed_product_title_v5']),norm('FÅRIKÅLKJØTT LAM'))
    def test_p25_halloween(self):self.assertEqual(norm(self.row('W41-P25-C001')['proposed_product_title_v5']),norm('HALLOWEENBØTTE'))
    def test_p28_multibuy(self):
        c=self.row('W41-P28-C002');self.assertEqual(c['proposed_product_title_v5'],'FARRIS / BRIS / FRUS');self.assertEqual(c['pricing_mode'],'MULTIBUY_CONDITIONAL')
    def test_p8_price_is_review_only(self):
        c=self.row('W41-P08-C001');self.assertEqual(c['printed_current_nok'],'19.99');self.assertEqual(c['price_alternative_nok_v5'],'19.90');self.assertFalse(c['publishable'])
    def test_p05_salame_unchanged(self):self.assertEqual(self.row('W41-P05-C002')['proposed_product_title_v5'],'MORTADELLA')
    def test_p02_klasetomat_unchanged(self):self.assertEqual(self.row('W41-P02-C004')['proposed_product_title_v5'],'KLASETOMAT')
    def test_p04_pizza_isolation(self):self.assertEqual(self.row('W41-P04-C003')['proposed_product_title_v5'],'RISTORANTE SPECIALE 2PK')
    def test_p14_pork_unchanged(self):self.assertEqual(self.row('W41-P14-C002')['proposed_product_title_v5'],'SVINEKJØTTDEIG 20 %')
    def test_p17_skinke_unchanged(self):self.assertEqual(self.row('W41-P17-C001')['proposed_product_title_v5'],'SKINKE XXL')
    def test_p06_pinsa_two_OCR_corroboration(self):self.assertEqual(self.row('W41-P06-C003')['proposed_product_title_v5'],'PINSA')
    def test_p28_grans_unresolved_not_fabricated(self):self.assertEqual(self.row('W41-P28-C001')['proposed_product_title_v5'],'GRANS')
    def test_unresolved_meat_category_not_guessed(self):self.assertEqual(self.row('W41-P16-C002')['proposed_product_title_v5'],'KYLLING K30TTBOLLER')
    def test_non_item_four(self):self.assertEqual(sum(z['pricing_mode'] in ('GRAPHIC_NOT_OFFER','COMPOSITE_TOTAL') for z in (refine(c,OCR) for c in DATA['candidates'])),4)
    def test_src_evidence_for_every_change(self):
        for c in DATA['candidates']:
            z=refine(c,OCR)
            if z['title_changed_v5'] and z['title_selection_reason_v5']!='MECHANICAL_REPEATED_OCR_SYLLABLE':self.assertTrue(z['title_evidence_v5'],c['candidate_id'])
    def test_no_input_mutation(self):
        c=MAP['W41-P08-C001'];s=copy.deepcopy(c);refine(c,OCR);self.assertEqual(s,c)
if __name__=='__main__':unittest.main()
