import unittest
from compare_w41_vision import vision_plaque_price, fuse

# Real plaque boxes from the held-aside W41 visual-audit file.
B6={'x0':.7914285714285715,'x1':.9342857142857143,'y0':.6567307692307692,'y1':.7538461538461538}
B12={'x0':.6357142857142857,'x1':.7785714285714286,'y0':.38942307692307687,'y1':.48557692307692313}
B20={'x0':.35714285714285715,'x1':.5,'y0':.12115384615384617,'y1':.21826923076923077}

def ln(text, x, y, w, h):
    return {'text':text, 'box':{'x':x,'y':y,'width':w,'height':h},'confidence':.91}

class PriceRecoveryV2Tests(unittest.TestCase):
    def test_p06_superscript_text_recovered_with_real_geometry(self):
        lines=[ln('74.⁹0',.8,.676923,.124286,.057692),ln('90',.878571,.708654,.042857,.025962)]
        r=vision_plaque_price(lines,B6)
        self.assertEqual(r['value'],'74.90')
        self.assertEqual(fuse(r['value'],'14.90')['status'],'ocr_disagreement_review_only')
        self.assertIsNone(fuse(r['value'],'14.90')['value'])

    def test_p12_compact_price_recovered_with_real_geometry(self):
        lines=[ln('10690',.648571,.419231,.12,.040385), ln('906',.665714,.386538,.048571,.086538)]
        r=vision_plaque_price(lines,B12)
        self.assertEqual(r['value'],'106.90')
        self.assertEqual(fuse(r['value'],'104.90')['status'],'ocr_disagreement_review_only')

    def test_p20_superscript_text_recovered_with_real_geometry(self):
        lines=[ln('44.⁹0',.367143,.145192,.125714,.049038),ln('90',.448571,.172115,.04,.024038)]
        r=vision_plaque_price(lines,B20)
        self.assertEqual(r['value'],'44.90')
        self.assertEqual(fuse(r['value'],'4.90')['status'],'ocr_disagreement_review_only')

    def test_outside_large_price_not_accepted(self):
        # Same numeric OCR line but outside any blue badge.
        self.assertIsNone(vision_plaque_price([ln('10690',.1,.419231,.12,.040385)],B12)['value'])

    def test_tiny_embedded_number_not_price(self):
        self.assertIsNone(vision_plaque_price([ln('10690',.680,.426,.03,.008)],B12)['value'])

    def test_low_width_embedded_number_not_price(self):
        self.assertIsNone(vision_plaque_price([ln('10690',.680,.426,.04,.042)],B12)['value'])

    def test_three_digit_code_not_accepted(self):
        self.assertIsNone(vision_plaque_price([ln('906',.650,.420,.12,.040)],B12)['value'])

    def test_ambiguous_compact_numbers_abstain(self):
        box={'x0':.1,'x1':.4,'y0':.1,'y1':.4}
        lines=[ln('10690',.115,.17,.155,.11),ln('10490',.2,.19,.155,.11)]
        got=vision_plaque_price(lines,box)
        self.assertIsNone(got['value'])
        self.assertEqual(got['status'],'vision_ambiguous_review_only')

    def test_nfkcs_are_not_applied_to_prior_prices(self):
        # The recovery routine is intentionally scoped ONLY to blue plaques.
        from compare_w41_vision import prior_signals
        self.assertEqual(prior_signals([ln('FØR 148,40-150,40',.1,.1,.5,.05)])[0]['kind'],'range')

if __name__=='__main__':unittest.main()
