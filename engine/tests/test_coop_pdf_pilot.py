"""Dependency-free, fail-closed synthetic tests for the 10-zone Coop pilot.

No real retailer PDF, images, raw price captures or priced product fixture in Git.
"""
import tempfile
import unittest
from pathlib import Path

from nutrivalue.providers.coop_pdf_pilot import (
    CARDS, SOURCE_SHA256, CardSpec, CoopPdfPilotError,
    _package_grams, extract_card, extract_pdf, save_private,
)


class FakePage:
    def __init__(self, spans):
        self.spans = spans

    def get_text(self, kind):
        if kind != 'dict':
            raise AssertionError('unexpected text mode')
        return {'blocks': [{'lines': [{'spans': self.spans}]}]}


def span(text, x, y, w, h, size):
    return {'text': text, 'bbox': (x, y, x + w, y + h), 'size': size}


SPEC = CardSpec('synthetic', 1, ('TEST PASTA',),
                (5, 5, 105, 25), (5, 30, 150, 70),
                (200, 10, 260, 110), (261, 10, 310, 110),
                'per_package', 'regular')


class CoopPdfPilotTests(unittest.TestCase):
    def test_ten_unique_offline_layout_scopes(self):
        self.assertEqual(len(CARDS), 10)
        self.assertEqual(len({x.key for x in CARDS}), 10)
        self.assertEqual(SOURCE_SHA256, '22051e42dd6891d1a9d2482e7a1b2c66b16f02297f101a2d3b2939aff10a4c29')

    def test_split_badge_is_exact_ore_not_unit_price_or_old_price(self):
        spans = [span('TEST PASTA', 10, 6, 92, 12, 12),
                 span('500 g. Pr pk. Pr kg 39,80 Førpris 27,90', 10, 32, 140, 12, 9.5),
                 span('19', 201, 30, 47, 70, 53),
                 span('90', 264, 34, 35, 28, 32),
                 span('19', 201, 30, 47, 70, 53)]  # identical PDF overprints de-duplicate
        result = extract_card(FakePage(spans), SPEC)
        self.assertEqual(result['display_price_ore'], 1990)
        self.assertEqual(result['package_grams_candidate'], 500)
        self.assertEqual(result['price_basis'], 'per_package')
        self.assertEqual(result['review_state'], 'source_only')
        self.assertEqual(result['independent_store_price_verification'], 'none')

    def test_badge_ambiguity_title_or_missing_cents_fails_closed(self):
        common = [span('TEST PASTA', 10, 6, 92, 12, 12),
                  span('500 g', 10, 32, 42, 12, 9.5),
                  span('19', 201, 30, 47, 70, 53),
                  span('90', 264, 34, 35, 28, 32)]
        for altered in (common[:-1], common + [span('20', 206, 40, 28, 65, 53)],
                        [span('TEST RICE', 10, 6, 92, 12, 12), *common[1:]]):
            with self.subTest(altered=altered), self.assertRaises(CoopPdfPilotError):
                extract_card(FakePage(altered), SPEC)

    def test_member_condition_must_be_sourced_not_inferred(self):
        member = CardSpec('synthetic_member', 1, ('TEST PASTA',),
                          SPEC.title_area, SPEC.details_area, SPEC.main_area,
                          SPEC.cents_area, 'per_package', 'member_only',
                          ('MEDLEMSPRIS', 'Ikke medlem?'))
        spans = [span('TEST PASTA', 10, 6, 92, 12, 12),
                 span('500 g', 10, 32, 42, 12, 9.5),
                 span('19', 201, 30, 47, 70, 53),
                 span('90', 264, 34, 35, 28, 32)]
        with self.assertRaisesRegex(CoopPdfPilotError, 'missing offer condition'):
            extract_card(FakePage(spans), member)
        spans.extend([span('MEDLEMSPRIS', 10, 155, 65, 11, 9.5),
                      span('Ikke medlem? 25,90', 10, 178, 90, 11, 9.5)])
        self.assertEqual(extract_card(FakePage(spans), member)['offer_kind'], 'member_only')

    def test_mass_guesses_are_not_promoted_for_range_or_approx(self):
        self.assertEqual(_package_grams('fra frittgående høner. 816 g. Pr pk.'), 816)
        for description in ('150-200 g. Pr pk.', '2 x ca. 1 kg',
                            'Fra 430 g', '450/600 g', 'Pr kg', '500 ml'):
            with self.subTest(description=description):
                self.assertIsNone(_package_grams(description))

    def test_wrong_pdf_rejected_before_dependency_or_output(self):
        with self.assertRaises(CoopPdfPilotError):
            extract_pdf(b'%PDF-wrong')
        with self.assertRaises(CoopPdfPilotError):
            extract_pdf(b'not pdf')
        with self.assertRaises(CoopPdfPilotError):
            extract_pdf(b'%PDF-' + b'X' * 16_000_001)

    def test_private_path_rejection_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'data/private'
            root.mkdir(parents=True)
            source = root / 'fixture.pdf'
            source.write_bytes(b'%PDF-fake')
            outside = Path(temp) / 'public.json'
            with self.assertRaisesRegex(CoopPdfPilotError, 'below data/private'):
                save_private(source, outside, root)
            self.assertFalse(outside.exists())
            with self.assertRaisesRegex(CoopPdfPilotError, 'checksum changed'):
                save_private(source, root / 'review.json', root)
            self.assertFalse((root / 'review.json').exists())


if __name__ == '__main__':
    unittest.main()
