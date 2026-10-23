import csv
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scan_w41_geometry import tsv_lines, priors_on_page, private_dest, PRIOR_PATTERN


class TSVAdapterTests(unittest.TestCase):
    def test_top_origin_becomes_bottom_origin_and_words_stay_joined(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'page-01.tsv'
            fields=['level','page_num','block_num','par_num','line_num','word_num',
                    'left','top','width','height','conf','text']
            with p.open('w',newline='',encoding='utf8') as f:
                writer=csv.DictWriter(f,fieldnames=fields,delimiter='\t');writer.writeheader()
                for n,x,width,txt in [(1,250,35,'FOR'),(2,290,55,'54,90')]:
                    writer.writerow({'level':5,'page_num':1,'block_num':4,'par_num':1,
                                     'line_num':1,'word_num':n,'left':x,'top':704,
                                     'width':width,'height':24,'conf':93,'text':txt})
            rows=tsv_lines(p,700,1040)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['text'],'FOR 54,90')
            self.assertAlmostEqual(rows[0]['box']['x'],250/700)
            self.assertAlmostEqual(rows[0]['box']['y'],1-728/1040)
            prior=priors_on_page(1,rows)
            self.assertEqual(prior[0]['ocr_prior_nok'],'54.90')
    def test_prior_requires_explicit_cents(self):
        self.assertIsNone(PRIOR_PATTERN.search('For 10 personer'))
        self.assertIsNotNone(PRIOR_PATTERN.search('FØR 39,90'))
        self.assertIsNotNone(PRIOR_PATTERN.search('FOR 54,90'))
    def test_private_output_restriction(self):
        self.assertTrue(private_dest(Path('/tmp/repo/data/private/tjek/test-out')))
        self.assertFalse(private_dest(Path('/tmp/repo/data/private/test-out')))

if __name__ == '__main__':
    unittest.main()
