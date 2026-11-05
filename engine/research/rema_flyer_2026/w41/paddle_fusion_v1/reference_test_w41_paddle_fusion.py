import copy
from pathlib import Path
import json
import inspect
import unittest
from w41_paddle_fusion import role_evidence, ISSUES, fullbox, clip, amt, run_fusion

FIX=Path(__file__).resolve().parent/'fixtures/w41-five-crops-text-and-boxes.json'
CROPS=json.loads(FIX.read_text(encoding='utf-8'))

class GeometryFusionTests(unittest.TestCase):
    def test_exact_prior_amount_required(self):
        l=CROPS['p13-issue-03-advert.png']['lines']
        self.assertEqual(role_evidence(l,'72.90','advert')['current'],'58.30')
        self.assertEqual(role_evidence(l,'84.90','advert')['current'],'67.90')
        self.assertIsNone(role_evidence(l,'30.90','advert')['prior'])

    def test_wide_crop_never_uses_lower_price(self):
        l=CROPS['p04-issue-01-advert.png']['lines']
        self.assertEqual(role_evidence(l,'103.00','advert')['current'],'72.10')
        self.assertEqual(role_evidence(l,'64.90','advert')['current'],'45.40')

    def test_paddle_full_crop_evidence(self):
        expected={(4,1):('103.00',None,'72.10'),(4,2):('64.90','45.40','45.40'),
                  (13,3):('84.90','67.90','67.90'),(20,4):('51.50','44.90','44.90'),
                  (20,5):('99.80','49.90',None)}
        for (page,index),(prior,price,advert) in expected.items():
            with self.subTest(page=page,issue=index):
                for mode,want in (('price',price),('advert',advert)):
                    lines=CROPS[f'p{page:02d}-issue-{index:02d}-{mode}.png']['lines']
                    pred=role_evidence(lines,prior,mode)
                    self.assertEqual(pred['prior'],prior)
                    self.assertEqual(pred['current'],want)

    def test_compact_price_is_not_pack_size(self):
        p=CROPS['p20-issue-05-price.png']['lines']
        result=role_evidence(p,'99.80','price')
        self.assertEqual(result['current'],'49.90')
        self.assertNotEqual(result['current'],'6.90')

    def test_conflicting_ocr_does_not_invent_cents(self):
        p=CROPS['p04-issue-01-price.png']['lines']
        pred=role_evidence(p,'103.00','price')
        self.assertIsNone(pred['current'])  # '10I' is not 10 without independent evidence
        self.assertEqual(pred['reason'],'no_unambiguous_current_glyph_pair')

    def test_reference_amounts_not_used_in_inference(self):
        import w41_paddle_fusion as m
        for f in (m.role_evidence,m.fullbox,m.run_fusion):
            source=inspect.getsource(f)
            self.assertNotIn('printed_current_nok',source)
            self.assertNotIn('visual_product_caption',source)
            self.assertNotIn('score(',source)

    def test_bbox_transform_inside_page(self):
        box={'x':.55,'y':.22,'width':.05,'height':.009}
        for mode in ('price','advert'):
            wh=(500,600)
            bb=fullbox([200,150,300,310],box,mode,wh)
            self.assertTrue(0<=bb['x']<bb['x']+bb['width']<=1)
            self.assertTrue(0<=bb['y']<bb['y']+bb['height']<=1)

    def test_refuse_nonfinite_money_and_zero(self):
        self.assertIsNone(amt('0.00'))
        self.assertIsNone(amt('104,49'))
        self.assertIsNone(amt('nan'))
        self.assertEqual(amt('72.10'),'72.10')

    def test_brand_merged_price_refused(self):
        lines=[{'text':'FOR103.00','box':[180,120,300,145]},
               {'text':'Ristoran 720','box':[215,160,395,310]},
               {'text':'690g.104,49pr.kg','box':[0,510,310,540]}]
        self.assertIsNone(role_evidence(lines,'103.00','price')['current'])

    def test_cents_can_be_two_distinct_glyphs(self):
        lines=[{'text':'FOR64,90','box':[180,110,300,145]},
               {'text':'45','box':[217,165,343,312]},
               {'text':'40','box':[336,175,396,246]}]
        self.assertEqual(role_evidence(lines,'64.90','price')['current'],'45.40')

    def test_badge_next_column_excluded(self):
        lines=[{'text':'FOR51,50','box':[180,110,300,145]},
               {'text':'44','box':[216,165,343,310]},
               {'text':'90','box':[337,175,399,252]},
               {'text':'37','box':[55,167,156,305]}]
        self.assertEqual(role_evidence(lines,'51.50','price')['current'],'44.90')

    def test_source_cases_fixed_and_small(self):
        self.assertEqual(len(ISSUES),5)
        self.assertEqual(set(ISSUES),{(4,1),(4,2),(13,3),(20,4),(20,5)})

if __name__=='__main__':unittest.main()
