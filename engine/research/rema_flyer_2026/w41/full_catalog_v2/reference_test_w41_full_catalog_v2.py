import copy
import unittest

from w41_full_catalog_v2 import (
    minor_price_pair,compact_price_glyph,caption_peer_conflict,composite_or_graphic,
    clean_title,title_score,attach_glyph,add_partial_price_anchors,
    classify_condition,unit_price_context,refine, title_options,
)


def box(x=.5,y=.4,w=.12,h=.05):
    return {'x':x,'y':y,'width':w,'height':h}


def line(text,b=None):
    return {'text':text,'box':b or box()}


def candidate(b=None):
    return {'candidate_source':'cv_large_blue','price_box':b or box(),'prior_box':None,
            'printed_current_nok':None,'price_evidence':[],'review_only':True}


class TestW41FullV2(unittest.TestCase):
    def test_split_major_cents(self):
        x=minor_price_pair([line('24',box(.8,.56,.07,.065)),line('90',box(.89,.59,.04,.029))])
        self.assertEqual([q['value'] for q in x],['24.90'])
    def test_no_invented_cents(self):
        self.assertEqual([],minor_price_pair([line('23',box(.55,.42,.07,.07))]))
    def test_dont_treat_pack_number_as_price(self):
        self.assertEqual([],minor_price_pair([line('6PK'),line('90',box(.60,.44,.04,.02))]))
    def test_syntax_glued_34_90(self):
        self.assertEqual('34.90',compact_price_glyph([line('Q34 90')])[0]['value'])
    def test_dont_parse_hyphenated_campaign(self):
        self.assertEqual([],compact_price_glyph([line('4 FOR 30')]))
    def test_dont_parse_pack_to_price(self):
        self.assertEqual([],compact_price_glyph([line('KVIKK 64990')]))
    def test_title_from_flyer_and_pack(self):
        self.assertEqual('LAKSEFILET U / SKINN',clean_title('LAKSEFILET U / SKINN Gode hav , 500 g , 158,40 pr . kg'))
    def test_title_from_BAMA_rate(self):
        self.assertEqual('AUTUMN CRISP DRUER',clean_title('AUTUMN CRISP DRUER BAMA , 59,90 pr . kg'))
    def test_supplier_is_not_a_product_title(self):
        self.assertEqual('Gode hav , 400 g , 219,75 pr . kg',clean_title('Gode hav , 400 g , 219,75 pr . kg'))
    def test_reject_pr_stk(self):
        self.assertLess(title_score('PR . STK',box(.4,.5),box(.4,.4),[]),0)
    def test_reject_prior_as_title(self):
        self.assertLess(title_score('FØR 69,90',box(.4,.5),box(.4,.4),[]),0)
    def test_reject_pant_as_title(self):
        self.assertLess(title_score('+ PANT',box(.4,.5),box(.4,.4),[]),0)
    def test_peer_nearer_to_caption(self):
        self.assertTrue(caption_peer_conflict(box(.7,.23,.12,.02),box(.6,.47,.12,.05),[box(.8,.24,.12,.05)]))
    def test_peer_different_column_and_row(self):
        self.assertFalse(caption_peer_conflict(box(.46,.38,.10,.015),box(.45,.50,.12,.05),[box(.90,.21,.10,.04)]))
    def test_same_price_separated_positions_kept(self):
        records=[candidate(box(.45,.2,.1,.05))]
        x={'box':box(.75,.2,.1,.05),'value':'34.90','status':'glyph_review_only','text':'Q34 90'}
        self.assertEqual('new',attach_glyph(x,records,'vision_glued_price'))
        self.assertEqual(len(records),2)
    def test_enrich_missing_cv_price(self):
        records=[candidate(box(.82,.65,.14,.07))]
        x={'box':box(.83,.65,.06,.07),'value':'24.90','status':'glyph_review_only','text':'24 + 90'}
        self.assertEqual('enriched',attach_glyph(x,records,'vision_split_price'))
        self.assertEqual(records[0]['printed_current_nok'],'24.90')
    def test_refuse_conflicting_price(self):
        records=[candidate(box(.8,.65,.15,.09))];records[0]['printed_current_nok']='19.99'
        x={'box':box(.81,.65,.07,.07),'value':'19.90','status':'glyph_review_only','text':'19 + 90'}
        self.assertEqual('enriched',attach_glyph(x,records,'vision_split_price'))
        self.assertTrue(records[0]['price_disagreement'])
        self.assertEqual(records[0]['printed_current_nok'],'19.99')
    def test_composite_meal_not_item(self):
        r=candidate(box(.12,.72,.16,.11))
        self.assertEqual(composite_or_graphic(r,[line('SLUTTSUMMEN',box(.11,.86,.17,.03))]),'COMPOSITE_MEAL_TOTAL_REVIEW_ONLY')
    def test_lock_price_graphic_not_offer(self):
        r=candidate(box(.56,.06,.25,.20))
        self.assertEqual(composite_or_graphic(r,[line('LÅST PRIS',box(.59,.10,.12,.1))]),'GRAPHIC_LOCKED_PRICE_LABEL_REVIEW_ONLY')
    def test_multibuy_preserved_as_conditional(self):
        r=candidate(box(.75,.78,.13,.08));r['printed_current_nok']='30.00'
        self.assertEqual(classify_condition(r,[line('4 FOR 30',box(.69,.81,.17,.08))]),'MULTIBUY_CONDITIONAL')
    def test_packaged_unit_price_not_conflict(self):
        r={'promotion_flags':['PRICE_PER_KG_BADGE_REVIEW_ONLY'],'printed_current_nok':'24.90','title_evidence_v2':[{'clean_title':'KIMS CHIPS','box':box(.7,.5,.16,.02)}],
           'proposed_product_text_v2':'KIMS CHIPS'}
        v=unit_price_context(r,[line('KIMS CHIPS Kims , 200 g , 124,50 pr . kg',box(.7,.49,.16,.015))])
        self.assertIsNotNone(v);self.assertFalse(v['differs_from_printed_ocr'])
    def test_unpacked_rate_conflict_not_overwrite(self):
        r={'promotion_flags':['PRICE_PER_KG_BADGE_REVIEW_ONLY'],'printed_current_nok':'19.99','title_evidence_v2':[{'clean_title':'MELON HONNING','box':box(.45,.45,.14,.02)}],
           'proposed_product_text_v2':'MELON HONNING'}
        v=unit_price_context(r,[line('BAMA , 19,90 pr kg',box(.45,.43,.15,.01))])
        self.assertIsNotNone(v);self.assertTrue(v['differs_from_printed_ocr']);self.assertEqual(r['printed_current_nok'],'19.99')
    def test_reject_wrong_week(self):
        with self.assertRaises(ValueError):refine({'week':'2026-W42'}, {'pages':[]})
    def test_unknown_title_not_autoverified(self):
        r={'printed_current_nok':None,'caption_candidates':[],'price_box':box(.4,.5),'prior_box':None}
        x,y=title_options(r,[],[r['price_box']]);self.assertEqual(y,'')

if __name__=='__main__':unittest.main()

class TestSplitMultibuy(unittest.TestCase):
    def test_2_for_separated_from_89(self):
        r=candidate(box(.8,.49,.13,.08));r['printed_current_nok']='89.00'
        self.assertEqual(classify_condition(r,[line('2 FOR',box(.73,.56,.15,.045))]),'MULTIBUY_CONDITIONAL')
    def test_4for_without_separator(self):
        r=candidate(box(.73,.79,.13,.08));r['printed_current_nok']='30.00'
        self.assertEqual(classify_condition(r,[line('4FOR',box(.65,.87,.13,.03))]),'MULTIBUY_CONDITIONAL')
