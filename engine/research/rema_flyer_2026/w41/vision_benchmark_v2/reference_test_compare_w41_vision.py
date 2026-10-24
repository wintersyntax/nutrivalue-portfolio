import unittest
from compare_w41_vision import fuse, vision_plaque_price, checked_vision, prior_signals, run, prior_amount_coverage

B = {'x0':.20,'x1':.40,'y0':.20,'y1':.40}

def line(text,x,y,w=.05,h=.03):
    return {'text':text,'box':{'x':x,'y':y,'width':w,'height':h},'confidence':.95}

class VisionFusionTests(unittest.TestCase):
    def test_explicit_price(self):
        self.assertEqual(vision_plaque_price([line('34,90',.25,.25)], B)['value'],'34.90')
    def test_split_price_geometry(self):
        self.assertEqual(vision_plaque_price([line('34',.22,.25),line('90',.35,.32,w=.025)],B)['value'],'34.90')
    def test_reject_disagreeing_prices(self):
        got=vision_plaque_price([line('34,90',.22,.25),line('39,90',.28,.31)],B)
        self.assertIsNone(got['value'])
    def test_dont_accept_nearby_price_outside(self):
        self.assertIsNone(vision_plaque_price([line('99,90',.55,.25)],B)['value'])
    def test_dont_treat_label_as_price(self):
        self.assertIsNone(vision_plaque_price([line('LÅST PRIS',.25,.25)],B)['value'])
    def test_lone_number_is_not_price(self):
        self.assertIsNone(vision_plaque_price([line('34',.25,.25)],B)['value'])
    def test_conflict_abstains(self):
        self.assertEqual(fuse('74.90','14.90')['status'],'ocr_disagreement_review_only')
        self.assertIsNone(fuse('74.90','14.90')['value'])
    def test_agreement_is_review_only(self):
        self.assertEqual(fuse('34.90','34.90')['status'],'dual_engine_agreement_review_only')
    def test_range_preserved(self):
        result=prior_signals([line('FØR 148,40-150,40',.22,.25)])
        self.assertEqual(result[0]['kind'],'range')
        self.assertEqual(result[0]['amounts_nok_review_only'],['148.40','150.40'])
    def test_reject_week_leakage(self):
        with self.assertRaises(ValueError):checked_vision({'format':'nutrivalue_ocr_lines_v1','week':'2026-W42','page_count':29})
    def test_partial_not_accepted(self):
        doc={'format':'nutrivalue_ocr_lines_v1','provider':'google_cloud_vision','week':'2026-W41','page_count':29,
             'pages':[{'page':i,'status':'processed' if i<29 else 'pending','lines':[]} for i in range(1,30)]}
        with self.assertRaises(ValueError):checked_vision(doc)
    def test_prior_range_and_duplicate_not_double_counted(self):
        refs={'week':'2026-W41','record_count':39,'records':[{'page':1,'printed_prior_nok':'54.90','prior_kind':'single'}]*39}
        vision={'1':[{'amounts_nok_review_only':['54.90']}]*2}
        red={'week':'2026-W41','records':[{'page':1,'amounts_nok_review_only':['54.90']}]*4}
        out=prior_amount_coverage(refs,vision,red)
        self.assertEqual(out['union_matched_by_page_amount_only'],4)

    def test_visual_ref_not_inference(self):
        vis={'catalog_week':'2026-W41','rows':[{'page':1,'blue_plaque_index':0,'visual_label_nok':'34.90','blue_plaque_box':B}]*61}
        local={'week':'2026-W41','pages_scanned':29,'pages':[{'page':1,'observations':[{'blue_plaque_index':0,'current_price_nok_review_only':None}]}]}
        doc={'format':'nutrivalue_ocr_lines_v1','provider':'google_cloud_vision','week':'2026-W41','page_count':29,
             'pages':[{'page':i,'status':'processed','lines':[]} for i in range(1,30)]}
        out,_,counts=run(vis,local,doc)
        self.assertEqual(counts.get('correct',0),0)
        self.assertEqual(counts.get('abstained'),61)

if __name__=='__main__':unittest.main()
