import hashlib
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from overlap_audit import sha,read_manifest,grayscale,image_mae,classify,ocr_stats

class TestOverlap(unittest.TestCase):
    def test_same_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x';p.write_bytes(b'hello')
            self.assertEqual(sha(p),hashlib.sha256(b'hello').hexdigest())
    def test_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'h';p.write_text('a'*64+'  foo.jpg\n')
            self.assertEqual(read_manifest(p),{Path('foo.jpg'):'a'*64})
    def test_bad_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'h';p.write_text('not valid\n')
            with self.assertRaises(ValueError):read_manifest(p)
    def test_duplicate_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'h';p.write_text(('a'*64+'  x.jpg\n')*2)
            with self.assertRaises(ValueError):read_manifest(p)
    def test_classes(self):
        self.assertEqual(classify(True,0),'EXACT_SHA256')
        self.assertEqual(classify(False,.2),'NEAR_PIXEL_MATCH_REVIEW')
        self.assertEqual(classify(False,5),'LAYOUT_OVERLAP_REVIEW')
        self.assertEqual(classify(False,20),'PAGE_WIDE_DIFFERENCE_REVIEW')
    def test_grayscale_mae(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); p=d/'x.jpg';q=d/'y.jpg'
            Image.new('RGB',(700,1040),'red').save(p)
            Image.new('RGB',(700,1040),'blue').save(q)
            self.assertEqual(image_mae(grayscale(p),grayscale(p)),0)
            self.assertGreater(image_mae(grayscale(p),grayscale(q)),0)
    def test_image_dimension_guard(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.jpg';Image.new('RGB',(12,12)).save(p)
            with self.assertRaises(ValueError):grayscale(p)
    def test_ocr_stats(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'p.tsv';t=Path(d)/'p.txt'
            p.write_text('level\ttext\tconf\n5\tBREAD\t88.0\n5\t14\t55.0\n4\tignored\t99\n')
            t.write_text('BREAD 14')
            s=ocr_stats(p,t)
            self.assertEqual(s['ocr_word_tokens'],2)
            self.assertEqual(s['ocr_digit_tokens'],1)
            self.assertEqual(s['ocr_median_conf'],71.5)
if __name__=='__main__':unittest.main()
