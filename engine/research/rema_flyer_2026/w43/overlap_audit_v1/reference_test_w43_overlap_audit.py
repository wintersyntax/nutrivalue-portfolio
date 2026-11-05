import csv
import json
import unittest
from pathlib import Path

import numpy as np
from w43_overlap_audit import compare_gray, VISUAL_REUSE, EXPECTED, PAGE_RE

ROOT=Path(__file__).resolve().parent

class AuditTests(unittest.TestCase):
    def test_identical_gray(self):
        a=np.arange(64*96,dtype=np.uint8).reshape(96,64)
        self.assertEqual(compare_gray(a,a)[0],0)
        self.assertAlmostEqual(compare_gray(a,a)[1],1,places=3)
    def test_expected_pages(self):
        self.assertEqual(EXPECTED['W43'],25)
        self.assertEqual(EXPECTED['W41'],29)
    def test_page_names(self):
        self.assertEqual(PAGE_RE.search('pages/page-25.jpg').group(1),'25')
        self.assertIsNone(PAGE_RE.search('page-99.json'))
    def test_csv_count(self):
        with (ROOT/'page-pairs.csv').open(newline='',encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),25)
        self.assertEqual([int(r['w43_page']) for r in rows],list(range(1,26)))
        self.assertTrue(all(1<=int(r['w41_review_page'])<=29 for r in rows))
        self.assertEqual(sum(r['preview_flag']=='VISIBLE_REUSED_CAMPAIGN_OR_OFFER' for r in rows),len(VISUAL_REUSE))
    def test_summary(self):
        x=json.loads((ROOT/'summary.json').read_text())
        self.assertEqual(x['pages'],{'w43':25,'w41':29})
        self.assertEqual(x['byte_identical_pairs'],0)
        self.assertEqual(x['independent_product_plus_price_accuracy'],'NOT_MEASURED')
    def test_all_previews(self):
        self.assertEqual(len(list((ROOT/'previews').glob('w43-*.jpg'))),25)
        self.assertEqual(len(list((ROOT/'previews').glob('w41-*.jpg'))),29)
    def test_html(self):
        text=(ROOT/'review-overlap.html').read_text()
        self.assertEqual(text.count('class="card"'),25)
        self.assertIn('data-kind="repeat"',text)

if __name__=='__main__':unittest.main()
