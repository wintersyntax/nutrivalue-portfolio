import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from w41_full_catalog import (
    amount,validate_v3,text_price_rows,rect_to_box,near_price,
    classify_plaque_price,link_captions,score_review,promotion_flags,
    CANDIDATE_FIELDS, PAGE_FIELDS, MISS_FIELDS
)

class FullCatalogTests(unittest.TestCase):
    def box(self,x=.2,y=.4,w=.1,h=.05):return dict(x=x,y=y,width=w,height=h)
    def vision(self,text,box=None):return dict(text=text,box=box or self.box(),confidence=.98)
    def fixture(self,source='v3_for_anchor',i=1):
        return {'candidate_id':f'W41-P01-C{i:03d}', 'candidate_source':source,'page':1}
    def test_price_excluded_from_regular_product_text(self):
        xs=[self.vision('39.90'),self.vision('REMA 1000 600 g, 39,90 pr. kg'),self.vision('FØR 54,90')]
        self.assertEqual([x['value'] for x in text_price_rows(xs)],['39.90'])
    def test_unreadable_and_small_text_are_excluded(self):
        xs=[self.vision('9990',self.box(h=.009)),self.vision('5990',self.box(w=.02)),self.vision('ROSTER 5990')]
        self.assertEqual([],text_price_rows(xs))
    def test_spatial_matching_never_merges_same_price_on_other_ad(self):
        a=self.box(.2,.4);b=self.box(.7,.4)
        self.assertFalse(near_price(a,b))
    def test_spatial_matching_same_box(self):
        a=self.box(.2,.4);b=self.box(.22,.42)
        self.assertTrue(near_price(a,b))
    def test_cv_detects_unique_price(self):
        r={'x0':.2,'x1':.35,'y0':.4,'y1':.5}
        hits=[{'index':0,'value':'54.90','box':self.box(.21,.41,.10,.045)}]
        v,state,_=classify_plaque_price(r,hits)
        self.assertEqual(v,'54.90')
        self.assertEqual(state,'plaque_vision_price_review_only')
    def test_cv_flags_conflicting_price(self):
        r={'x0':.2,'x1':.35,'y0':.4,'y1':.5}
        hits=[{'index':0,'value':'54.90','box':self.box(.21,.41,.10,.045)},
              {'index':1,'value':'54.99','box':self.box(.21,.43,.10,.04)}]
        v,state,_=classify_plaque_price(r,hits)
        self.assertIsNone(v);self.assertEqual(state,'multiple_ocr_prices_review_only')
    def test_large_price_without_neighbor_caption(self):
        self.assertEqual([],link_captions([],self.box(),[]))
    def test_caption_does_not_span_far_column(self):
        p=self.box(.72,.4,.1,.05)
        lines=[self.vision('TIRAMISU, 170 g',self.box(.15,.2,.15,.02))]
        self.assertEqual([],link_captions(lines,p,[p]))
    def test_caption_below_price(self):
        p=self.box(.72,.4,.1,.05)
        lines=[self.vision('TIRAMISU 170 g',self.box(.7,.29,.16,.02))]
        self.assertIn('TIRAMISU',link_captions(lines,p,[p])[0]['text'])
    def test_campaign_without_price_not_assumed_price(self):
        x=self.vision('-50 %',self.box(.4,.8,.2,.15))
        self.assertEqual([],text_price_rows([x]))
    def test_multibuy_not_unit_price(self):
        p=self.box(.72,.82,.13,.06)
        lines=[self.vision('4FOR',self.box(.60,.87,.11,.03)),self.vision('+ PANT',self.box(.88,.8,.09,.01))]
        self.assertIn('MULTIBUY_OR_PANT_CONDITION_REVIEW_ONLY',promotion_flags(lines,p))
    def test_genuine_standalone_kg_badge_flag(self):
        p=self.box(.7,.62,.13,.08)
        lines=[self.vision('PR KG',self.box(.82,.64,.05,.02))]
        self.assertIn('PRICE_PER_KG_BADGE_REVIEW_ONLY',promotion_flags(lines,p))
    def test_product_description_with_kg_not_flagged(self):
        p=self.box(.7,.62,.13,.08)
        lines=[self.vision('Kjeldsberg , 300g, 149 pr kg',self.box(.7,.56,.16,.01))]
        self.assertNotIn('PRICE_PER_KG_BADGE_REVIEW_ONLY',promotion_flags(lines,p))
    def test_reject_wrong_v3_week(self):
        with self.assertRaises(ValueError):validate_v3({'schema':'x','week':'2026-W42'},'x')
    def test_reject_wrong_v3_provenance(self):
        with self.assertRaises(ValueError):validate_v3({'schema':'nutrivalue_w41_ab_evidence_v3_paddle_review_only','week':'2026-W41',
            'pages':29,'tjek_catalog_linked':False,'visual_labels_used_in_inference':False,
            'tjek_identity_verified':False,'database_writes':False,'v3_source_sha256':{'vision':'other'},
            'candidates':[]},'expected')
    def test_no_score_with_empty_review(self):
        doc={'candidates':[self.fixture()]}
        reviews=[dict(candidate_id='W41-P01-C001',is_ad='',price_correct='',product_correct='',duplicate_of='')]
        pages=[{'page':str(i),'review_complete':''} for i in range(1,30)]
        x=score_review(doc,reviews,pages,[])
        self.assertEqual(x['status'],'NOT_MEASURED_REVIEW_INCOMPLETE')
        self.assertIsNone(x['advert_discovery_recall'])
    def test_no_score_if_page_unchecked(self):
        doc={'candidates':[self.fixture()]}
        reviews=[dict(candidate_id='W41-P01-C001',is_ad='YES',price_correct='YES',product_correct='YES',duplicate_of='')]
        pages=[{'page':str(i),'review_complete':'YES' if i!=21 else ''} for i in range(1,30)]
        x=score_review(doc,reviews,pages,[])
        self.assertEqual(x['status'],'NOT_MEASURED_REVIEW_INCOMPLETE')
    def test_full_score_with_missed_ad(self):
        doc={'candidates':[self.fixture()]}
        reviews=[dict(candidate_id='W41-P01-C001',is_ad='YES',price_correct='YES',product_correct='NO',duplicate_of='')]
        pages=[{'page':str(i),'review_complete':'YES'} for i in range(1,30)]
        missed=[{'page':'2','product':'UNDETECTED OFFER','printed_current_nok':'19.90'}]
        x=score_review(doc,reviews,pages,missed)
        self.assertEqual(x['status'],'FULL_CATALOG_RECALL_MEASURED')
        self.assertEqual(x['advert_discovery_recall'],.5)
        self.assertEqual(x['correct_product_and_price_recall'],0)
    def test_suspect_duplicate_cannot_inflate_score(self):
        doc={'candidates':[self.fixture(i=1),self.fixture(i=2)]}
        reviews=[dict(candidate_id='W41-P01-C001',is_ad='YES',price_correct='YES',product_correct='YES',duplicate_of=''),
                 dict(candidate_id='W41-P01-C002',is_ad='DUPLICATE',price_correct='',product_correct='',duplicate_of='W41-P01-C001')]
        pages=[{'page':str(i),'review_complete':'YES'} for i in range(1,30)]
        x=score_review(doc,reviews,pages,[])
        self.assertEqual(x['reference_ads_denominator'],1)
        self.assertEqual(x['duplicate_candidates'],1)
    def test_duplicate_missing_target_fails(self):
        doc={'candidates':[self.fixture()]}
        reviews=[dict(candidate_id='W41-P01-C001',is_ad='DUPLICATE',duplicate_of='fake')]
        pages=[{'page':str(i),'review_complete':'YES'} for i in range(1,30)]
        with self.assertRaises(ValueError):score_review(doc,reviews,pages,[])
    def test_invalid_page_missed_ad_fails(self):
        doc={'candidates':[self.fixture()]}
        reviews=[dict(candidate_id='W41-P01-C001',is_ad='NO',price_correct='',product_correct='',duplicate_of='')]
        pages=[{'page':str(i),'review_complete':'YES'} for i in range(1,30)]
        with self.assertRaises(ValueError):score_review(doc,reviews,pages,[{'page':'30','product':'X'}])
    def test_w41_campaign_pages_can_have_zero_prices(self):
        self.assertEqual([],text_price_rows([self.vision('PRISKUTT -50 %')]))
    def test_unicode_price_handling(self):
        self.assertEqual('74.90',text_price_rows([self.vision('74.⁹0')])[0]['value'])

if __name__=='__main__':unittest.main()
