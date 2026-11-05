import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('targeted',Path(__file__).with_name('run_last_two.py'))
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class Result:
    @property
    def json(self):
        return {'res':{'rec_texts':['23','90','COUSCOUS'],
                       'rec_scores':[.89,.98,.9],
                       'rec_boxes':[[0,0,20,40],[21,20,41,40],[0,80,75,90]]}}
class Model:
    def predict(self,p):return [Result()]

class Tests(unittest.TestCase):
    def test_issues(self):self.assertEqual(set(mod.CROPS),{'p02-couscous','p16-egg6pk'})
    def test_modes(self):self.assertTrue(all(set(v)=={'price','advert'} for v in mod.CROPS.values()))
    def test_version(self):self.assertEqual(mod.WEEK,'2026-W41')
    def test_line_mapping(self):self.assertEqual(mod.get_lines(Result())[1]['text'],'90')
    def test_line_box(self):self.assertEqual(mod.get_lines(Result())[0]['box'],[0,0,20,40])
    def test_no_money_imputation(self):
        with tempfile.TemporaryDirectory() as d:
            q=mod.run([('p02-couscous','price',Path(d)/'image.png','xx')],Path(d)/'o',Model())
            self.assertEqual([x['text'] for x in q['crops'][0]['lines']],['23','90','COUSCOUS'])
            self.assertNotIn('confirmed_price_nok',json.dumps(q))
    def test_output_is_review_only(self):
        with tempfile.TemporaryDirectory() as d:
            result=mod.run([('p02-couscous','price',Path(d)/'image.png','xx')],Path(d)/'o',Model())
            self.assertTrue(result['review_only'])
            self.assertTrue((Path(d)/'o'/'audit.md').exists())
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):
                mod.run([],Path(d),Model())
    def test_reject_wrong_response(self):
        with self.assertRaises(ValueError):mod.get_lines({'invalid':'yes'}) if False else mod.get_lines(None)
    def test_invalid_list(self):
        with self.assertRaises(ValueError):mod.get_lines({'res':{'rec_texts':'not a list'}})

if __name__=='__main__':unittest.main()
