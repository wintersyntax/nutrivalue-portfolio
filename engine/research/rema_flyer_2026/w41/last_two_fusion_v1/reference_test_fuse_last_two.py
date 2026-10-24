import copy, unittest
from fuse_last_two import fuse, price_in_crop, verify_paddle, verify_source, TARGETS

def line(t,b,score=.998):return {'text':t,'box':b,'confidence':score}

def crops_for(issue, major, name, cents='90'):
    t=TARGETS[issue];c=[]
    for mode in ('price','advert'):
        fn,h=t[mode]
        lines=[line(major,[200,200,330,365]),line(cents,[319,211,379,292])]
        if mode=='advert':lines.append(line(name,[0,400,180,430]))
        c.append({'issue':issue,'mode':mode,'file':fn,'image_sha256':h,'lines':lines})
    return c

def dataset():
    c=[]
    for i in range(102):c.append({'candidate_id':f'OTHER-{i}', 'printed_current_nok':'12.90'})
    for issue,major,title in [('p02-couscous','23','COUSCOUS'),('p16-egg6pk','26','EGG 6PK')]:
        t=TARGETS[issue]
        c.append({'candidate_id':t['candidate_id'],'page':t['page'],'candidate_source':'vision_partial_price_fragment',
                  'printed_current_nok':None,'pricing_mode':'PRICE_UNREADABLE_REVIEW_ONLY',
                  'source_evidence':{'read_major_only':major},'guarded_product_title_v3':title,
                  'review_only':True,'publishable':False,'tjek_identity_verified':False})
    return {'week':'2026-W41','pages':29,'review_only':True,'schema':'test-v3', 'candidates':c}

def paddle():
    crops=crops_for('p02-couscous','23','COUSCOUS')+crops_for('p16-egg6pk','26','EGG6PK')
    return {'schema':'nutrivalue_w41_last_two_paddle_review_only_v1','week':'2026-W41','review_only':True,'image_count':4,'crops':crops}

class Tests(unittest.TestCase):
    def test_success(self):
        v3=dataset();f,p=fuse(v3,paddle())
        self.assertEqual(len(f['candidates']),104)
        self.assertEqual([r['paddle_price_candidate_nok'] for r in p],['23.90','26.90'])
        for r in f['candidates'][-2:]:
            self.assertIsNone(r['printed_current_nok'])
            self.assertFalse(r['publishable'])
            self.assertFalse(r['price_accepted_v4'])
        self.assertIsNone(f['full_catalog_recall'])
        self.assertIsNone(v3['candidates'][-1].get('targeted_paddle_price_candidate_nok_v4'))
    def test_cents_from_right_not_left(self):
        lines=[line('90',[70,211,110,291]),line('26',[200,200,330,365]),line('90',[319,211,379,292])]
        self.assertEqual(price_in_crop(lines,'26')[0],'26.90')
    def test_reject_no_cents(self):
        with self.assertRaises(ValueError):price_in_crop([line('23',[200,200,330,365])],'23')
    def test_reject_misaligned(self):
        with self.assertRaises(ValueError):price_in_crop([line('23',[200,200,330,365]),line('90',[319,500,379,600])],'23')
    def test_reject_low_confidence(self):
        with self.assertRaises(ValueError):price_in_crop([line('23',[200,200,330,365]),line('90',[319,211,379,292],.4)],'23')
    def test_reject_mismatch_between_crops(self):
        p=paddle();p['crops'][0]['lines'][1]['text']='80'
        with self.assertRaises(ValueError):fuse(dataset(),p)
    def test_reject_missing_caption(self):
        p=paddle();p['crops'][1]['lines']=p['crops'][1]['lines'][:2]
        with self.assertRaises(ValueError):fuse(dataset(),p)
    def test_reject_hash(self):
        p=paddle();p['crops'][0]['image_sha256']='0'*64
        with self.assertRaises(ValueError):verify_paddle(p)
    def test_reject_duplicate_crop(self):
        p=paddle();p['crops'][1]=copy.deepcopy(p['crops'][0])
        with self.assertRaises(ValueError):verify_paddle(p)
    def test_reject_week(self):
        p=paddle();p['week']='2026-W42'
        with self.assertRaises(ValueError):verify_paddle(p)
    def test_reject_other_catalog(self):
        v3=dataset();v3['week']='2026-W42'
        with self.assertRaises(ValueError):verify_source(v3)
    def test_reject_prepriced(self):
        v3=dataset();v3['candidates'][-1]['printed_current_nok']='26.90'
        with self.assertRaises(ValueError):verify_source(v3)
    def test_reject_w41_identity(self):
        v3=dataset();v3['candidates'][-1]['tjek_identity_verified']=True
        with self.assertRaises(ValueError):verify_source(v3)
    def test_reject_unexpected_ocr_major(self):
        v3=dataset();v3['candidates'][-2]['source_evidence']['read_major_only']='23.90'
        with self.assertRaises(ValueError):verify_source(v3)
    def test_reject_wrong_product(self):
        v3=dataset();v3['candidates'][-1]['guarded_product_title_v3']='SUGAR'
        with self.assertRaises(ValueError):verify_source(v3)
if __name__=='__main__':unittest.main()
