"""Offline contracts for a single immutable first-party MENY PDF pilot."""
import tempfile
import unittest
from pathlib import Path

from nutrivalue.providers.meny_pdf_pilot import (
    PDF_SHA256, SPECS, MenyPdfPilotError, OfferSpec,
    _package_candidate, extract_offer, extract_pdf, save_private,
)


class FakePage:
    def __init__(self, spans):
        self.spans = spans

    def get_text(self, kind):
        if kind != 'dict':
            raise AssertionError('unexpected mode')
        return {'blocks': [{'lines': [{'spans': self.spans}]}]}


def span(text, x, y, w, h, font_size):
    return {'text': text, 'bbox': (x, y, x + w, y + h),
            'size': font_size}


PRICE_SPEC = OfferSpec('test_fish', 1, ('TEST FISH',),
                       (5, 45, 135, 75), (5, 76, 160, 105),
                       (195, 50, 261, 120), (260, 50, 310, 99),
                       (290, 100, 350, 130), 'per_package', 'weekly',
                       (5, 5, 135, 34), 'UKENS TILBUD')


def price_spans(unit='/ PK'):
    return [span('UKENS TILBUD', 10, 9, 110, 12, 10),
            span('TEST FISH', 10, 50, 92, 16, 10),
            span('500g. Førpris 55,00', 10, 80, 140, 12, 9),
            span('39', 205, 58, 42, 55, 53),
            span('90', 266, 60, 34, 30, 31),
            span(unit, 300, 106, 30, 16, 11.7)]


class MenyPdfPilotTests(unittest.TestCase):
    def test_ten_disjoint_source_cards_and_pinned_hash(self):
        self.assertEqual(len(SPECS), 10)
        self.assertEqual(len({x.key for x in SPECS}), 10)
        self.assertEqual(PDF_SHA256,
            '4178b086ca994bdf3cdda9ce89c2506023b53100ccedc620ddfe411a9b520f36')
        self.assertEqual({s.page for s in SPECS}, {1, 2, 3})

    def test_source_badge_split_integers_not_struck_out_price(self):
        spans = price_spans()
        result = extract_offer(FakePage(spans + [spans[3]]), PRICE_SPEC)
        self.assertEqual(result['display_price_ore'], 3990)
        self.assertEqual(result['package_quantity_candidate'], 500)
        self.assertEqual(result['package_unit_candidate'], 'g')
        self.assertEqual(result['review_state'], 'source_only')
        self.assertEqual(result['store_price_verification'], 'none')

    def test_missing_label_invalid_unit_and_ambiguous_price_fail_closed(self):
        base = price_spans()
        for case in (base[1:], price_spans('/ STK'),
                     base + [span('42', 204, 57, 42, 55, 53)],
                     [span('TEST MEAT', 10, 50, 92, 16, 10), *base[1:]]):
            with self.subTest(case=case), self.assertRaises(MenyPdfPilotError):
                extract_offer(FakePage(case), PRICE_SPEC)

    def test_3_for_2_without_base_price_keeps_price_null(self):
        spec = OfferSpec('test_oat', 1, ('Oatly',),
                         PRICE_SPEC.heading_box, PRICE_SPEC.details_box,
                         (190, 50, 311, 125), None, None,
                         'unknown_no_single_price', 'ongoing_3_for_2',
                         PRICE_SPEC.offer_label_box, 'FASTE KNALLKJØP')
        data = [span('FASTE KNALLKJØP', 10, 9, 110, 12, 10),
                span('PLUKK & MIKS', 210, 54, 80, 13, 12),
                span('Oatly', 10, 50, 80, 16, 10),
                span('alle varianter', 10, 80, 140, 12, 9),
                span('3 2', 205, 58, 60, 55, 53),
                span('FOR', 218, 77, 30, 30, 26),
                span('33% RABATT VED KJØP AV 3', 210, 111, 90, 8, 7)]
        actual = extract_offer(FakePage(data), spec)
        self.assertIsNone(actual['display_price_ore'])
        self.assertEqual(actual['conditional_deal'],
                         'buy_3_pay_for_2_no_base_price')
        with self.assertRaises(MenyPdfPilotError):
            extract_offer(FakePage(data[:-1]), spec)

    def test_real_oatly_layout_accepts_fragmented_badge_glyphs(self):
        # Reproduces the exact flyer region, with 3 / FOR / 2 split
        # and differently segmented 33% footnote as PyMuPDF may emit.
        spec = SPECS[-1]
        page = FakePage([
            span('FASTE KNALLKJØP', 392, 156, 66, 12, 9.4),
            span('Oatly', 340, 256, 28, 13, 10),
            span('alle varianter', 340, 268, 58, 11, 9),
            span('PLUKK & MIKS', 402, 177, 67, 15, 12.6),
            span('3', 400, 181, 24, 66, 53.5),
            span('2', 447, 181, 25, 66, 53.5),
            span('FOR', 420, 195, 32, 39, 25.8),
            span('33 %', 400, 237, 23, 9, 7),
            span('RABATT VED KJØP AV 3', 425, 237, 49, 9, 7),
        ])
        result = extract_offer(page, spec)
        self.assertEqual(result['source_name'], 'Oatly')
        self.assertEqual(result['offer_kind'], 'ongoing_3_for_2')
        self.assertIsNone(result['display_price_ore'])

    def test_real_oatly_region_missing_evidence_still_rejected(self):
        spec = SPECS[-1]
        core = [
            span('FASTE KNALLKJØP', 392, 156, 66, 12, 9.4),
            span('Oatly', 340, 256, 28, 13, 10),
            span('alle varianter', 340, 268, 58, 11, 9),
            span('PLUKK & MIKS', 402, 177, 67, 15, 12.6),
            span('3 2', 400, 181, 73, 66, 53.5),
            span('FOR', 420, 195, 32, 39, 25.8),
            span('33% RABATT VED KJØP AV 3', 400, 237, 73, 9, 7),
        ]
        for missing in (3, 4, 5, 6):
            with self.subTest(missing=missing), self.assertRaisesRegex(
                    MenyPdfPilotError, 'incomplete 3-for-2 source badge'):
                extract_offer(FakePage(
                    core[:missing] + core[missing + 1:]), spec)
        # Evidence in a neighboring product card must not pass.
        distant = core[:6] + [
            span('33% RABATT VED KJØP AV 3', 500, 237, 73, 9, 7)]
        with self.assertRaises(MenyPdfPilotError):
            extract_offer(FakePage(distant), spec)

    def test_approximate_range_and_volumetric_mass_are_distinct(self):
        self.assertEqual(_package_candidate('500G, TINE'), (500, 'g', None))
        self.assertEqual(_package_candidate('1l, Fersk&Ferdig'), (1000, 'ml', None))
        self.assertEqual(_package_candidate('15stk, S/M'), (None, None, 15))
        for desc in ('ca 900g, 4pk', '500-570g', '250-300g, 4 oster',
                     '300ml/1l'):
            with self.subTest(desc=desc):
                self.assertEqual(_package_candidate(desc), (None, None, None))

    def test_unrecognized_pdf_or_wrong_digest_fails_before_parse(self):
        for raw in (b'', b'hello', b'%PDF-invented',
                    b'%PDF-' + b'X' * 32_000_001):
            with self.subTest(length=len(raw)), self.assertRaises(MenyPdfPilotError):
                extract_pdf(raw)

    def test_private_path_and_hash_protection_no_public_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'data/private'
            root.mkdir(parents=True)
            pdf = root / 'flyer.pdf'
            pdf.write_bytes(b'%PDF-made-up')
            out = root / 'cards.json'
            with self.assertRaisesRegex(MenyPdfPilotError, 'below data/private'):
                save_private(pdf, Path(td) / 'public.json', root)
            self.assertFalse((Path(td) / 'public.json').exists())
            with self.assertRaisesRegex(MenyPdfPilotError, 'checksum changed'):
                save_private(pdf, out, root)
            self.assertFalse(out.exists())
            out.write_text('immutable')
            with self.assertRaisesRegex(MenyPdfPilotError, 'report exists'):
                save_private(pdf, out, root)
            self.assertEqual(out.read_text(), 'immutable')


if __name__ == '__main__':
    unittest.main()
