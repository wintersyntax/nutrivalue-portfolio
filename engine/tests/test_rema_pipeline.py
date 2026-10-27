"""Synthetic offline REMA tests; no private flyer fixtures or external services."""
import csv
import hashlib
import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from nutrivalue.rema.inputs import ValidationError, load_catalog
from nutrivalue.rema.ocr import normalize_tsv, read_cache
from nutrivalue.rema.parser import parse_page
from nutrivalue.rema.pipeline import process_catalog


def digest(data):
    return hashlib.sha256(data).hexdigest()


def png(width=400, height=600):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * width * 3) * height)) + chunk(b'IEND', b''))


def tsv():
    return ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
            '1\t1\t0\t0\t0\t0\t0\t0\t400\t600\t-1\t\n'
            '5\t1\t1\t1\t1\t1\t120\t120\t50\t50\t90\t19,90\n'
            '5\t1\t2\t1\t1\t1\t100\t250\t100\t10\t95\tTESTFISK\n'
            '5\t1\t3\t1\t1\t1\t100\t270\t150\t10\t90\t500 g, 39,80 pr. kg\n').encode()


def line(text, x=.2, y=.5, w=.2, h=.02, confidence=95):
    return {'text': text, 'box': {'x': x, 'y': y, 'width': w, 'height': h},
            'confidence': confidence, 'words': []}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'data/private/tjek'
        self.root.mkdir(parents=True)
        self.metadata = self.root / 'catalog.json'
        self.manifest = self.root / 'images.json'
        self.cache = self.root / 'cache'
        self.output = self.root / 'review'
        self.cache.mkdir()
        self.make_catalog()

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def make_catalog(self, n=1, catalog_id='alternate-catalog', week='2032-W09'):
        self.write(self.metadata, {'id': catalog_id, 'dealer': {'id': 'retailer', 'name': 'REMA 1000'},
                                   'page_count': n, 'week': week, 'offer_count': 999})
        images, cached = [], []
        for number in range(1, n + 1):
            name = f'image-{number}.blob'
            raw = png()
            (self.root / name).write_bytes(raw)
            images.append({'page': number, 'file': name, 'sha256': digest(raw)})
            name = f'ocr-{number}.tsv'
            (self.cache / name).write_bytes(tsv())
            cached.append({'page': number, 'image_sha256': digest(raw), 'tsv_file': name,
                           'tsv_sha256': digest(tsv()), 'recognized_lines': 3})
        self.write(self.manifest, {'catalog_id': catalog_id, 'week': week, 'pages': images[::-1]})
        self.write(self.cache / 'ocr-manifest.json', {
            'schema': 'nutrivalue_rema_ocr_manifest_v1', 'catalog_id': catalog_id, 'week': week,
            'source_manifest_sha256': digest(self.manifest.read_bytes()), 'tesseract_version': 'synthetic',
            'language': 'nor+eng', 'psm': 11, 'page_count': n,
            'total_recognized_lines': n * 3, 'pages': cached})

    def catalog(self):
        return load_catalog(self.metadata, self.manifest)

    def run_pipeline(self):
        return process_catalog(self.metadata, self.manifest, self.output, ocr_cache=self.cache)


class InputTests(Fixture):
    def test_arbitrary_page_counts_and_order(self):
        for n in (1, 3, 25, 29):
            self.make_catalog(n)
            self.assertEqual([p['page'] for p in self.catalog()['pages']], list(range(1, n + 1)))

    def test_missing_duplicate_and_extra_pages_fail_closed(self):
        for pages in ([], [1, 1], [1, 2]):
            self.make_catalog()
            d = json.loads(self.manifest.read_text())
            d['pages'] = [dict(d['pages'][0], page=p) for p in pages]
            self.write(self.manifest, d)
            with self.assertRaises(ValidationError):
                self.catalog()

    def test_hash_and_missing_image_rejected(self):
        p = self.root / 'image-1.blob'
        p.write_bytes(p.read_bytes() + b'changed')
        with self.assertRaises(ValidationError): self.catalog()
        p.unlink()
        with self.assertRaises(ValidationError): self.catalog()

    def test_wrong_catalog_and_dealer_rejected(self):
        d = json.loads(self.metadata.read_text())
        d['id'] = 'other'
        self.write(self.metadata, d)
        with self.assertRaises(ValidationError): self.catalog()
        self.make_catalog()
        d = json.loads(self.metadata.read_text())
        d['dealer']['name'] = 'Other retailer'
        self.write(self.metadata, d)
        with self.assertRaises(ValidationError): self.catalog()

    def test_traversal_and_symlink_images_rejected(self):
        d = json.loads(self.manifest.read_text())
        d['pages'][0]['file'] = '../image-1.blob'
        self.write(self.manifest, d)
        with self.assertRaises(ValidationError): self.catalog()
        self.make_catalog()
        p = self.root / 'image-1.blob'
        p.rename(self.root / 'original')
        p.symlink_to(self.root / 'original')
        with self.assertRaises(ValidationError): self.catalog()


class OCRTests(Fixture):
    def test_normalized_bottom_left_lines_and_words(self):
        p = normalize_tsv(tsv().decode(), 1, 400, 600)
        self.assertEqual(len(p['lines']), 3)
        self.assertEqual(p['lines'][0]['text'], '19,90')
        self.assertAlmostEqual(p['lines'][0]['box']['y'], 1 - 170 / 600)
        self.assertEqual(p['lines'][0]['words'][0]['text'], '19,90')

    def test_cache_identity_hash_counts_schema_and_truncation(self):
        for field, value in [('catalog_id', 'wrong'), ('source_manifest_sha256', '0' * 64),
                             ('total_recognized_lines', 4), ('schema', 'unknown'), ('page_count', 2)]:
            self.make_catalog()
            path = self.cache / 'ocr-manifest.json'
            d = json.loads(path.read_text()); d[field] = value; self.write(path, d)
            with self.assertRaises(ValidationError): read_cache(self.catalog(), self.cache)
        self.make_catalog()
        (self.cache / 'ocr-1.tsv').write_bytes(tsv()[:-15])
        with self.assertRaises(ValidationError): read_cache(self.catalog(), self.cache)

    def test_cache_validated_without_calling_tesseract(self):
        with patch('subprocess.run', side_effect=AssertionError('OCR must not run')):
            pages, _ = read_cache(self.catalog(), self.cache)
        self.assertEqual(len(pages[0]['lines']), 3)

    def test_hashed_quote_folding_is_audited_without_losing_ocr_rows(self):
        raw = tsv() + (
            '5\t1\t4\t1\t1\t1\t100\t300\t50\t10\t90\t"fish\n'
            '5\t1\t5\t1\t1\t1\t100\t320\t50\t10\t90\t"bread\n').encode()
        (self.cache / 'ocr-1.tsv').write_bytes(raw)
        path = self.cache / 'ocr-manifest.json'
        data = json.loads(path.read_text())
        data['pages'][0].update(tsv_sha256=digest(raw), recognized_lines=4)
        data['total_recognized_lines'] = 4
        self.write(path, data)
        pages, provenance = read_cache(self.catalog(), self.cache)
        self.assertEqual(len(pages[0]['lines']), 5)
        self.assertEqual(provenance['declared_lines'], 4)
        self.assertEqual(provenance['normalization_warnings'][0]['kind'], 'quoted_tsv_row_folding')
        self.assertFalse(any('\n' in l['text'] for l in pages[0]['lines']))

    def test_cache_preserves_explicitly_unrecorded_engine_settings(self):
        p = self.cache / 'ocr-manifest.json'
        data = json.loads(p.read_text()); data.update(language='unrecorded', psm=None)
        self.write(p, data)
        pages, origin = read_cache(self.catalog(), self.cache)
        self.assertEqual(len(pages), 1)
        self.assertIsNone(origin['psm'])
        self.assertIn('ocr_settings_unrecorded', [w['kind'] for w in origin['normalization_warnings']])

    def test_truncated_geometry_and_nonfinite_ocr_fail(self):
        for raw in (tsv().decode().rstrip('\n'), tsv().decode().replace('400\t600', '401\t600'),
                    tsv().decode().replace('90\t19,90', 'nan\t19,90')):
            with self.assertRaises(ValidationError): normalize_tsv(raw, 1, 400, 600)


class ParserTests(unittest.TestCase):
    def parse(self, *lines):
        return parse_page({'page': 1, 'width': 400, 'height': 600, 'lines': list(lines)})

    def test_split_ore_not_neighbour_price(self):
        ads = self.parse(line('19', .1, .7, .1, .08), line('90', .215, .74, .06, .035),
                         line('FISK', .1, .55), line('500 g, 39,80 pr. kg', .1, .52),
                         line('29,90', .65, .7, .15, .08), line('BRØD', .65, .55),
                         line('500 g, 59,80 pr. kg', .65, .52))['adverts']
        fish = next(a for a in ads if a['product_name'] == 'FISK')
        self.assertEqual(fish['current_price_nok'], '19.90')
        self.assertEqual(len(ads), 2)

    def test_former_unit_and_current_are_distinct(self):
        a = self.parse(line('FØR 29,90', .1, .8), line('19,90', .1, .7, .15, .08),
                       line('FISK', .1, .55), line('500 g, 39,80 pr. kg', .1, .52))['adverts'][0]
        self.assertEqual(a['current_price_nok'], '19.90')
        self.assertEqual(a['former_prices_nok'], ['29.90'])
        self.assertEqual(a['unit_prices'][0]['amount_nok'], '39.80')

    def test_multibuy_and_member_discount_do_not_become_single_prices(self):
        a = self.parse(line('3 FOR 50,-', .1, .7, .2, .07), line('MEDLEM', .1, .65),
                       line('BRØD', .1, .55), line('500 g', .1, .52))['adverts'][0]
        self.assertIsNone(a['current_price_nok'])
        self.assertEqual(a['multibuy']['quantity'], 3)
        self.assertEqual(a['multibuy']['total_nok'], '50.00')
        self.assertTrue(a['conditions'])
        b = self.parse(line('-20%', .1, .7, .2, .07), line('FISK', .1, .55),
                       line('500 g', .1, .52))['adverts'][0]
        self.assertIsNone(b['current_price_nok'])
        self.assertEqual(b['percentage_discounts'], ['20'])

    def test_missing_price_and_prior_only_are_retained(self):
        a = self.parse(line('FISK', .1, .55), line('500 g, 39,80 pr. kg', .1, .52))['adverts'][0]
        self.assertIn('missing_printed_current_price', a['issues'])
        self.assertIsNone(a['current_price_nok'])
        a = self.parse(line('FØR 29,90', .1, .7), line('FISK', .1, .55),
                       line('500 g', .1, .52))['adverts'][0]
        self.assertEqual(a['former_prices_nok'], ['29.90'])
        self.assertIsNone(a['current_price_nok'])

    def test_ambiguous_titles_and_split_prices_flagged(self):
        result = self.parse(line('19', .1, .7, .1, .08), line('90', .215, .74, .05, .03),
                            line('50', .22, .69, .05, .03), line('FISK', .1, .55),
                            line('500 g', .1, .52))
        self.assertTrue(any('ambiguous' in i for a in result['adverts'] for i in a['issues']))

    def test_advertising_copy_is_not_a_product(self):
        result = self.parse(line('ALLTID LAVE PRISER'), line('SKANN FOR OPPSKRIFT'),
                            line('REMA 1000'), line('200', .7, .8, .1, .05))
        self.assertEqual(result['adverts'], [])
        self.assertTrue(result['unassigned_lines'])


class PipelineTests(Fixture):
    def test_exports_complete_offline_and_review_only(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('sqlite3.connect', side_effect=AssertionError('database forbidden')), \
             patch('subprocess.run', side_effect=AssertionError('cached OCR must not run')):
            report = self.run_pipeline()
        self.assertEqual(report['ocr_lines'], 3)
        self.assertEqual(report['accuracy']['status'], 'NOT_MEASURED_REVIEW_INCOMPLETE')
        self.assertTrue((self.output / 'review.html').is_file())
        self.assertTrue((self.output / 'pages/page-0001.svg').is_file())
        adverts = json.loads((self.output / 'adverts.json').read_text())['adverts']
        self.assertTrue(adverts)
        self.assertTrue(all(a['verification_status'] == 'review_only' for a in adverts))
        self.assertEqual(len(list((self.output / 'adverts').glob('*.json'))), len(adverts))
        with self.assertRaises(ValidationError): self.run_pipeline()

    def test_repeat_products_in_different_catalogs_retained(self):
        self.run_pipeline()
        first = json.loads((self.output / 'adverts.json').read_text())['adverts']
        self.make_catalog(catalog_id='next-catalog', week='2032-W10')
        self.output = self.root / 'next-review'
        self.run_pipeline()
        second = json.loads((self.output / 'adverts.json').read_text())['adverts']
        self.assertEqual(len(first), len(second))
        self.assertNotEqual(first[0]['id'], second[0]['id'])

    def test_invalid_cache_leaves_no_partial_output(self):
        (self.cache / 'ocr-1.tsv').write_text('invalid')
        with self.assertRaises(ValidationError): self.run_pipeline()
        self.assertFalse(self.output.exists())

    def test_private_output_boundary(self):
        self.output = Path(self.tmp.name).resolve() / 'public-review'
        with self.assertRaises(ValidationError): self.run_pipeline()
        self.assertFalse(self.output.exists())

class SemanticEdgeTests(unittest.TestCase):
    parse = ParserTests.parse

    def test_unit_labels_and_deposits_are_never_product_titles(self):
        result = self.parse(line('19,90', .2, .7, .1, .08), line('PR. KG', .2, .6),
                            line('+ PANT', .2, .5), line('PR. KOPP', .2, .4))
        self.assertTrue(all(a['product_name'] is None for a in result['adverts']))

    def test_standalone_unit_marker_owns_its_numeric_badge(self):
        result = self.parse(line('19,90', .2, .7, .1, .08), line('PR. KG', .2, .6),
                            line('FISK', .2, .45), line('500 g', .2, .42))
        fish = next(a for a in result['adverts'] if a['product_name'] == 'FISK')
        self.assertIsNone(fish['current_price_nok'])
        self.assertEqual(fish['unit_prices'][0]['amount_nok'], '19.90')

    def test_low_confidence_digits_remain_unresolved(self):
        a = self.parse(line('2', .1, .7, .1, .08, confidence=15),
                       line('90', .215, .74, .06, .035), line('FISK', .1, .55),
                       line('500 g', .1, .52))['adverts'][0]
        self.assertIsNone(a['current_price_nok'])
        self.assertIn('low_confidence_current_price', a['issues'])

    def test_prior_range_is_not_two_accepted_former_prices(self):
        a = self.parse(line('FØR 29,90-39,90', .1, .8), line('19,90', .1, .7, .15, .08),
                       line('FISK', .1, .55), line('500 g', .1, .52))['adverts'][0]
        self.assertEqual(a['former_prices_nok'], [])
        self.assertIn('unresolved_former_price_range', a['issues'])

    def test_multibuy_without_total_and_distinct_product_association(self):
        a = self.parse(line('KJØP 3 BETAL FOR 2', .1, .7, .2, .07), line('BRØD', .1, .55),
                       line('500 g', .1, .52))['adverts'][0]
        self.assertEqual(a['multibuy']['pay_for'], 2)
        self.assertIsNone(a['multibuy']['total_nok'])

class MoreSemanticTests(unittest.TestCase):
    parse = ParserTests.parse

    def test_small_composition_percentage_is_not_a_discount(self):
        result = self.parse(line('100%', .2, .6, .04, .008), line('78% FULLKORN', .2, .5))
        self.assertEqual(result['adverts'], [])
        self.assertTrue(result['unassigned_signals'])

    def test_multiline_bundle_price_preserves_total_and_quantity(self):
        a = self.parse(line('3 FOR', .1, .8, .12, .04), line('50,-', .1, .72, .15, .06),
                       line('BRØD', .1, .55), line('500 g', .1, .52))['adverts'][0]
        self.assertIsNone(a['current_price_nok'])
        self.assertEqual(a['multibuy']['quantity'], 3)
        self.assertEqual(a['multibuy']['total_nok'], '50.00')

    def test_raised_ore_digits_are_normalized_before_integer_parsing(self):
        for text in ('55⁹⁰', '55⁹0'):
            a = self.parse(line(text, .1, .7, .15, .06), line('FISK', .1, .55),
                           line('500 g', .1, .52))['adverts'][0]
            self.assertEqual(a['current_price_nok'], '55.90')

    def test_separate_prior_marker_owns_its_digits(self):
        a = self.parse(line('FØR', .1, .8, .06, .025), line('29', .17, .8, .06, .03),
                       line('19,90', .1, .7, .15, .06), line('FISK', .1, .55),
                       line('500 g', .1, .52))['adverts'][0]
        self.assertEqual(a['former_prices_nok'], ['29.00'])
        self.assertEqual(a['current_price_nok'], '19.90')


class SafetyAndReplayTests(Fixture):
    def test_local_ocr_contract_and_cache_replay(self):
        from types import SimpleNamespace
        from nutrivalue.rema.ocr import run_tesseract
        with patch('subprocess.run', side_effect=[SimpleNamespace(stdout='tesseract synthetic\n'),
                                                SimpleNamespace(stdout=tsv())]) as runner:
            pages, origin = run_tesseract(self.catalog())
        self.assertEqual(origin['mode'], 'local_tesseract')
        self.assertEqual(pages[0]['lines'][0]['text'], '19,90')
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args.args[0][:3], ['tesseract', str(self.root / 'image-1.blob'), 'stdout'])
        self.assertNotIn('shell', runner.call_args.kwargs)

    def test_unknown_ocr_engine_settings_do_not_change_replay(self):
        d = json.loads((self.cache / 'ocr-manifest.json').read_text())
        d['psm'] = 99; self.write(self.cache / 'ocr-manifest.json', d)
        with self.assertRaises(ValidationError): read_cache(self.catalog(), self.cache)

    def test_deterministic_replay(self):
        self.run_pipeline()
        first = self.output
        self.output = self.root / 'review-again'
        self.run_pipeline()
        self.assertEqual({str(p.relative_to(first)):digest(p.read_bytes()) for p in first.rglob('*') if p.is_file()},
                         {str(p.relative_to(self.output)):digest(p.read_bytes()) for p in self.output.rglob('*') if p.is_file()})

    def test_reexported_ocr_is_a_valid_cache(self):
        self.run_pipeline()
        pages, origin = read_cache(self.catalog(), self.output / 'ocr')
        self.assertEqual(len(pages[0]['lines']), 3)
        self.assertEqual(origin['mode'], 'validated_cache')

    def test_symlink_cache_and_output_parent_rejected(self):
        p = self.cache / 'ocr-1.tsv'; p.rename(self.cache / 'original')
        p.symlink_to(self.cache / 'original')
        with self.assertRaises(ValidationError): read_cache(self.catalog(), self.cache)
        folder = self.root / 'linked'; folder.symlink_to(self.cache, target_is_directory=True)
        self.output = folder / 'new'
        with self.assertRaises(ValidationError): self.run_pipeline()

    def test_missing_cache_page_and_wrong_image_hash_rejected(self):
        for change in ('page', 'image'):
            self.make_catalog()
            p = self.cache / 'ocr-manifest.json'; d = json.loads(p.read_text())
            if change == 'page': d['pages'] = []
            else: d['pages'][0]['image_sha256'] = '0' * 64
            self.write(p, d)
            with self.assertRaises(ValidationError): read_cache(self.catalog(), self.cache)

    def test_duplicate_json_keys_and_unsupported_input_schema_rejected(self):
        self.metadata.write_text('{"id":"first","id":"second"}')
        with self.assertRaises(ValidationError): self.catalog()
        self.make_catalog()
        d = json.loads(self.metadata.read_text()); d['schema'] = 'not-supported'; self.write(self.metadata, d)
        with self.assertRaises(ValidationError): self.catalog()

    def test_failed_ocr_and_changed_image_during_export_leave_no_output(self):
        from nutrivalue.rema.ocr import run_tesseract
        import subprocess
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('tesseract', 1)):
            with self.assertRaises(ValidationError): run_tesseract(self.catalog())
        self.assertFalse(self.output.exists())
        from nutrivalue.rema import pipeline
        original = pipeline.export_review
        def change_image(*args):
            (self.root / 'image-1.blob').write_bytes(b'changed')
            return original(*args)
        with patch.object(pipeline, 'export_review', side_effect=change_image):
            with self.assertRaises(ValidationError): self.run_pipeline()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.root / '.review.lock').exists())

    def test_cli_success_and_fail_closed_exit_codes(self):
        import subprocess
        import sys
        script = Path(__file__).resolve().parents[1] / 'scripts/process_rema_flyer.py'
        command = [sys.executable, str(script), '--catalog', str(self.metadata),
                   '--manifest', str(self.manifest), '--ocr-cache', str(self.cache),
                   '--output', str(self.output)]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['pages_processed'], 1)
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Output collision', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_html_escapes_source_and_csv_neutralizes_formulas(self):
        from nutrivalue.rema.review import safe_csv
        self.assertEqual(safe_csv('=1+1'), "'=1+1")
        self.assertEqual(safe_csv('@SUM(1)'), "'@SUM(1)")
        self.assertEqual(safe_csv('plain'), 'plain')
        raw = tsv().replace(b'TESTFISK', b'<SCRIPT>FISK</SCRIPT>')
        (self.cache / 'ocr-1.tsv').write_bytes(raw)
        p = self.cache / 'ocr-manifest.json'; d = json.loads(p.read_text())
        d['pages'][0]['tsv_sha256'] = digest(raw); self.write(p,d)
        self.run_pipeline()
        html = (self.output / 'review.html').read_text()
        self.assertNotIn('<SCRIPT>',html)
        self.assertIn('&lt;SCRIPT&gt;',html)
