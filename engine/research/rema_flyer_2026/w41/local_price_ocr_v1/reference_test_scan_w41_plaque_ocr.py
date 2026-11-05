import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

from scan_w41_plaque_ocr import (
    CENTS_PART,PRICE_PART,consensus,inspect_plaque,plaque_crop,is_private_dir,bbox_diff
)

class SplitPlaqueOCRTests(unittest.TestCase):
    def test_consensus_two_complete_digits_required(self):
        r=[{'raw':'90'},{'raw':'90'},{'raw':'00'}]
        self.assertEqual(consensus(r,CENTS_PART),'90')
        self.assertIsNone(consensus([{'raw':'90'},{'raw':'0'}],CENTS_PART))
        self.assertIsNone(consensus([{'raw':'90'}],CENTS_PART))

    def test_main_rejects_long_product_numbers(self):
        self.assertIsNone(consensus([{'raw':'3990'},{'raw':'3990'}],PRICE_PART))

    def test_reconstructs_only_when_both_parts_consistent(self):
        # Mock OCR engine, but exercise the actual view/consensus/pairing logic.
        readings=['34','34','90','90']
        with patch('scan_w41_plaque_ocr.tesseract_digits',side_effect=readings) as mock:
            o=inspect_plaque(np.zeros((110,110,3),dtype=np.uint8))
        self.assertEqual(mock.call_count,4)
        self.assertEqual(o['current_price_nok_review_only'],'34.90')
        self.assertEqual(o['status'],'plaque_split_digits_candidate_review_only')

    def test_rejects_unreadable_cents(self):
        with patch('scan_w41_plaque_ocr.tesseract_digits',side_effect=['29','29','9','0','9']):
            o=inspect_plaque(np.zeros((110,110,3),dtype=np.uint8))
        self.assertIsNone(o['current_price_nok_review_only'])
        self.assertTrue(o['status'].startswith('unresolved'))

    def test_rejects_unreadable_main(self):
        with patch('scan_w41_plaque_ocr.tesseract_digits',side_effect=['2','9','29','90','90']):
            o=inspect_plaque(np.zeros((110,110,3),dtype=np.uint8))
        self.assertIsNone(o['current_price_nok_review_only'])

    def test_bottom_origin_bbox_crop(self):
        im=np.zeros((100,100,3),dtype=np.uint8)
        im[60:80,20:50]=111
        b={'x0':.2,'x1':.5,'y0':.2,'y1':.4}
        roi=plaque_crop(im,b)
        self.assertEqual(roi.shape,(20,30,3))
        self.assertTrue((roi==111).all())

    def test_coordinates_match_stable_parser(self):
        a={'x0':.2,'x1':.5,'y0':.2,'y1':.4}
        b=dict(a);b['x0']+=.001
        self.assertLess(bbox_diff(a,b),.003)

    def test_output_private_guard(self):
        self.assertTrue(is_private_dir(Path('/tmp/nutrivalue/data/private/tjek/out')))
        self.assertFalse(is_private_dir(Path('/tmp/nutrivalue/data/private/public/out')))

if __name__=='__main__':unittest.main()
