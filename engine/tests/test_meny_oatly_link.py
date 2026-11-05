"""Offline fail-closed contracts for PDF Oatly campaign -> first-party item price joins."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from nutrivalue.providers.meny_oatly_link import (
    TARGETS, OatlyLinkError, _price_from_title,
    analyze_product, join_oatly,
)
from nutrivalue.providers.meny_pdf_pilot import PDF_SHA256
from nutrivalue.providers.source_probe import SOURCES


def page(source='meny_oatly_barista_500', *, main='29,90',
         unit='59,80', title_price='29,90', offer='3 for 2',
         campaign='Plukk & miks Oatly produkter', product=None,
         extras=''):
    details = {
        'meny_oatly_barista_500': ('Barista Edition 0.5l Oatly', '29,90', '59,80'),
        'meny_oatly_vanilla_1000': ('Vanilla 1l Oatly', '49,90', '49,90'),
        'meny_oatly_churros_1000': ('Churros Flavour 1l Oatly', '54,90', '54,90'),
        'meny_oatly_chocolate_250': ('Sjokolade 250ml Oatly', '15,90', '63,60'),
    }
    label, price, unit_price = details[source]
    if source != 'meny_oatly_barista_500':
        main, unit, title_price = price, unit_price, price
    label = product if product is not None else label
    primary = main.replace(',', ' ') + ' kr'
    content = f'''<!doctype html><html><head>
    <title>Kjøp Havredrikk. {label} hos MENY - {title_price} kr | MENY</title>
    </head><body><h1>Havredrikk</h1>
    <div>{label}</div><div>{primary}</div>
    <span>{unit} kr/l</span><span>{offer}</span><span>Faste knallkjøp</span>
    <span>{campaign}</span><button>Kjøp</button>
    <h2>Om produktet</h2>{extras}</body></html>'''
    return content.encode('utf-8')


STAMP = '2026-10-11T01:53:00+00:00'


class MenyOatlyJoinTests(unittest.TestCase):
    def test_all_pinned_hosts_urls_and_product_keys_distinct(self):
        self.assertEqual(len(TARGETS), 4)
        self.assertEqual(len({t.source for t in TARGETS}), 4)
        self.assertEqual(len({SOURCES[t.source].url for t in TARGETS}), 4)
        self.assertTrue(all(SOURCES[t.source].url.startswith(
            'https://meny.no/varer/meieri-egg/') for t in TARGETS))

    def test_first_party_title_main_price_and_unit_agree(self):
        first = analyze_product('meny_oatly_barista_500', page(),
                                captured_at=STAMP, retrieval='public_http')
        self.assertEqual(first['base_price_ore_source_only'], 2990)
        self.assertEqual(first['net_volume_ml_from_page_label'], 500)
        self.assertEqual(first['store_scope'], 'not_selected_or_verified')
        self.assertEqual(first['retailer_url_product_key_not_verified_ean'],
                         '7394376619939')
        for src, value in [
                ('meny_oatly_vanilla_1000', 4990),
                ('meny_oatly_churros_1000', 5490),
                ('meny_oatly_chocolate_250', 1590)]:
            with self.subTest(source=src):
                item = analyze_product(src, page(src), captured_at=STAMP,
                                       retrieval='public_http')
                self.assertEqual(item['base_price_ore_source_only'], value)

    def test_page_title_only_and_recommendation_price_never_prove_main_price(self):
        raw = page()
        source_only = raw.replace(b'<div>29 90 kr</div>', b'<div>Laster produkter</div>')
        with self.assertRaises(OatlyLinkError):
            analyze_product('meny_oatly_barista_500', source_only,
                            captured_at=STAMP, retrieval='public_http')
        wrong_main = page(main='39,90', extras='<p>29,90 kr</p>')
        with self.assertRaisesRegex(OatlyLinkError, 'differs'):
            analyze_product('meny_oatly_barista_500', wrong_main,
                            captured_at=STAMP, retrieval='public_http')

    def test_campaign_terms_identity_and_unit_rate_required(self):
        cases = [
            page(offer='Laster produkter'),
            page(campaign='Falske produktkort'),
            page(unit='50,00'),
            page(product='Barista Edition 1l Oatly'),
            page(title_price='19,90'),
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(OatlyLinkError):
                analyze_product('meny_oatly_barista_500', raw,
                                captured_at=STAMP, retrieval='public_http')

    def test_unsafe_timestamp_bad_encoding_price_and_source_rejected(self):
        for ts in ('2026-10-11', 'not a date', ''):
            with self.subTest(stamp=ts), self.assertRaises(OatlyLinkError):
                analyze_product('meny_oatly_barista_500', page(),
                                captured_at=ts, retrieval='public_http')
        with self.assertRaises(OatlyLinkError):
            analyze_product('meny_oatly_barista_500', b'\xff',
                            captured_at=STAMP, retrieval='public_http')
        with self.assertRaises(OatlyLinkError):
            analyze_product('other_source', page(),
                            captured_at=STAMP, retrieval='public_http')
        with self.assertRaises(OatlyLinkError):
            _price_from_title('Oatly 1000,90 kr', 'Oatly')

    def test_pdf_anchor_and_source_private_hash_then_immutable_join(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'data/private'
            pdf = root / 'meny-pdf-pilot/cards-review.json'
            pdf.parent.mkdir(parents=True)
            pdf.write_text(json.dumps({
                'pdf_sha256': PDF_SHA256,
                'catalogue_window': '2026-10-12/2026-10-17',
                'cards': [{'key': 'oatly_3for2', 'source_page': 3,
                           'source_name': 'Oatly', 'display_price_ore': None,
                           'conditional_deal': 'buy_3_pay_for_2_no_base_price',
                           'offer_kind': 'ongoing_3_for_2',
                           'review_state': 'source_only'}]}))
            source_root = root / 'source-probes'
            html = page()
            digest = hashlib.sha256(html).hexdigest()
            folder = source_root / 'meny_oatly_barista_500'
            folder.mkdir(parents=True)
            (folder / f'{digest}.html').write_bytes(html)
            metadata = {
                'source': 'meny_oatly_barista_500',
                'source_url': SOURCES['meny_oatly_barista_500'].url,
                'final_url': SOURCES['meny_oatly_barista_500'].url,
                'sha256': digest, 'retrieval': 'public_http',
                'fetched_at': STAMP,
            }
            info = folder / f'{digest}.json'
            info.write_text(json.dumps(metadata))
            out = root / 'meny-oatly-link/review.json'
            joined = join_oatly(pdf, source_root, out, root,
                                sources=['meny_oatly_barista_500'])
            self.assertEqual(joined['pdf_base_price_ore'], None)
            self.assertEqual(joined['verified_protein_value_rows'], 0)
            self.assertEqual(joined['eligible_online_product_price_candidates'][0]
                             ['base_price_ore_source_only'], 2990)
            self.assertEqual(len(joined['other_pinned_products_not_collected']), 3)
            self.assertTrue(out.is_file())
            with self.assertRaisesRegex(OatlyLinkError, 'never overwrite'):
                join_oatly(pdf, source_root, out, root,
                           sources=['meny_oatly_barista_500'])
            # Reject private HTML that no longer matches the original hash.
            out.unlink()
            (folder / f'{digest}.html').write_bytes(b'tampered')
            with self.assertRaisesRegex(OatlyLinkError, 'digest mismatch'):
                join_oatly(pdf, source_root, out, root,
                           sources=['meny_oatly_barista_500'])
            self.assertFalse(out.exists())
            # A locally symlinked source must not read outside ignored private data.
            source_html = folder / f'{digest}.html'
            source_html.unlink()
            outside = Path(d) / 'outside.html'
            outside.write_bytes(html)
            source_html.symlink_to(outside)
            with self.assertRaisesRegex(OatlyLinkError, 'outside private storage'):
                join_oatly(pdf, source_root, out, root,
                           sources=['meny_oatly_barista_500'])
            self.assertFalse(out.exists())

    @staticmethod
    def make_browser_fixture(root):
        """Exactly the owner render report shape, not a network mock."""
        from nutrivalue.providers.meny_oatly_link import _VisibleHTML
        folder = root / 'render-probes/meny-oatly-barista'
        folder.mkdir(parents=True)
        html = page().replace(b'<h1>Havredrikk</h1>',
                              b'<span>MENY Bryn</span><h1>Havredrikk</h1>')
        parsed = _VisibleHTML()
        parsed.feed(html.decode('utf-8'))
        parsed.close()
        screenshot = b'\x89PNG\r\n\x1a\nprivate screenshot synthetic'
        (folder / 'rendered.html').write_bytes(html)
        (folder / 'screenshot.png').write_bytes(screenshot)
        data = {
            'source': 'meny_oatly_barista_500',
            'requested_url': SOURCES['meny_oatly_barista_500'].url,
            'final_url': SOURCES['meny_oatly_barista_500'].url,
            'http_status': 200,
            'captured_at_utc': STAMP,
            'title': parsed.title,
            'rendered_html_sha256': hashlib.sha256(html).hexdigest(),
            'screenshot_sha256': hashlib.sha256(screenshot).hexdigest(),
            'rendered_html_bytes': len(html),
            'method': 'one_browser_navigation_six_second_wait_no_clicks',
            'evidence_state': 'browser_render_only_needs_owner_visual_review',
            'signals': {
                'visible_mentions_oatly': True,
                'visible_mentions_29_90': True,
                'visible_mentions_campaign': True,
                'visible_mentions_price_unit': True,
                'visible_mentions_loading': False,
                'verified_price': False,
                'store_selected': False,
                'product_campaign_price_join': 'not_attempted',
            },
        }
        (folder / 'report.json').write_text(json.dumps(data))
        return folder, data

    @staticmethod
    def write_pdf_anchor(root):
        pdf = root / 'meny-pdf-pilot/cards-review.json'
        pdf.parent.mkdir(parents=True)
        pdf.write_text(json.dumps({
            'pdf_sha256': PDF_SHA256,
            'catalogue_window': '2026-10-12/2026-10-17',
            'cards': [{'key': 'oatly_3for2', 'source_page': 3,
                       'source_name': 'Oatly', 'display_price_ore': None,
                       'conditional_deal': 'buy_3_pay_for_2_no_base_price',
                       'offer_kind': 'ongoing_3_for_2',
                       'review_state': 'source_only'}]}))
        return pdf

    def test_browser_source_exact_hash_and_pdf_join_keeps_store_unverified(self):
        from nutrivalue.providers.meny_oatly_link import _load_render_snapshot
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'data/private'
            pdf = self.write_pdf_anchor(root)
            folder, _ = self.make_browser_fixture(root)
            item = _load_render_snapshot(folder / 'report.json', root)
            self.assertEqual(item['base_price_ore_source_only'], 2990)
            self.assertEqual(item['retrieval'], 'playwright_render')
            self.assertEqual(item['store_displayed_in_retailer_ui'], 'MENY Bryn')
            self.assertEqual(item['store_scope'], 'not_selected_or_verified')
            self.assertIn('browser_screenshot_sha256', item)
            output = root / 'meny-oatly-link/browser-candidate.json'
            joined = join_oatly(pdf, root / 'source-probes', output, root,
                                sources=['meny_oatly_barista_500'],
                                render_report=folder / 'report.json')
            self.assertEqual(joined['pdf_base_price_ore'], None)
            self.assertEqual(joined['product_capture_provenance'],
                             'playwright_render')
            self.assertTrue(joined['browser_capture_not_equivalent_to_http_snapshot'])
            self.assertFalse(joined['store_price_verified'])
            self.assertEqual(joined['verified_protein_value_rows'], 0)
            self.assertEqual(len(joined['eligible_online_product_price_candidates']), 1)
            with self.assertRaisesRegex(OatlyLinkError, 'never overwrite'):
                join_oatly(pdf, root / 'source-probes', output, root,
                           sources=['meny_oatly_barista_500'],
                           render_report=folder / 'report.json')

    def test_browser_html_and_image_mutation_fail_closed_without_output(self):
        from nutrivalue.providers.meny_oatly_link import _load_render_snapshot
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'data/private'
            folder, _ = self.make_browser_fixture(root)
            raw = (folder / 'rendered.html').read_bytes()
            (folder / 'rendered.html').write_bytes(raw + b' changed')
            with self.assertRaisesRegex(OatlyLinkError, 'hash mismatch'):
                _load_render_snapshot(folder / 'report.json', root)
            (folder / 'rendered.html').write_bytes(raw)
            (folder / 'screenshot.png').write_bytes(b'changed image')
            with self.assertRaisesRegex(OatlyLinkError, 'hash mismatch'):
                _load_render_snapshot(folder / 'report.json', root)

    def test_browser_report_tampering_and_loading_skeleton_do_not_pass(self):
        from nutrivalue.providers.meny_oatly_link import _load_render_snapshot
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'data/private'
            folder, report = self.make_browser_fixture(root)
            for changed in (
                    dict(report, http_status=429),
                    dict(report, final_url='https://example.org/not-meny'),
                    dict(report, evidence_state='verified-price'),
                    dict(report, signals=dict(report['signals'],
                                              visible_mentions_29_90=False)),
                    dict(report, signals=dict(report['signals'],
                                              visible_mentions_loading=True)),
                    dict(report, signals=dict(report['signals'],
                                              verified_price=True))):
                with self.subTest(changed=changed), self.assertRaisesRegex(
                        OatlyLinkError, 'does not show required'):
                    (folder / 'report.json').write_text(json.dumps(changed))
                    _load_render_snapshot(folder / 'report.json', root)

    def test_browser_title_only_or_wrong_primary_price_does_not_pass(self):
        from nutrivalue.providers.meny_oatly_link import _load_render_snapshot
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'data/private'
            folder, report = self.make_browser_fixture(root)
            for modified in (
                    page(main='39,90'),
                    page().replace(b'<div>29 90 kr</div>',
                                   b'<div>Laster produkter</div>')):
                report['rendered_html_sha256'] = hashlib.sha256(modified).hexdigest()
                report['rendered_html_bytes'] = len(modified)
                (folder / 'rendered.html').write_bytes(modified)
                from nutrivalue.providers.meny_oatly_link import _VisibleHTML
                parsed = _VisibleHTML()
                parsed.feed(modified.decode('utf-8'))
                parsed.close()
                report['title'] = parsed.title
                (folder / 'report.json').write_text(json.dumps(report))
                with self.subTest(modified=modified), self.assertRaises(OatlyLinkError):
                    _load_render_snapshot(folder / 'report.json', root)

    def test_browser_requires_pinned_source_and_private_path(self):
        from nutrivalue.providers.meny_oatly_link import _load_render_snapshot
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'data/private'
            pdf = self.write_pdf_anchor(root)
            folder, _ = self.make_browser_fixture(root)
            with self.assertRaisesRegex(OatlyLinkError, 'pinned private'):
                _load_render_snapshot(Path(d) / 'report.json', root)
            with self.assertRaisesRegex(OatlyLinkError, 'only the pinned Barista'):
                join_oatly(pdf, root / 'source-probes',
                           root / 'meny-oatly-link/a.json', root,
                           sources=['meny_oatly_vanilla_1000'],
                           render_report=folder / 'report.json')

    def test_unapproved_source_duplicate_missing_pdf_and_public_output_fail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'data/private'
            root.mkdir(parents=True)
            pdf = root / 'missing.json'
            src = root / 'source-probes'
            out = root / 'join.json'
            for sources in (
                    ['meny_oatly_barista_500'] * 2,
                    ['meny_oatly_barista_500', 'evil'],
                    []):
                with self.subTest(sources=sources), self.assertRaises(OatlyLinkError):
                    join_oatly(pdf, src, out, root, sources=sources)
            with self.assertRaisesRegex(OatlyLinkError, 'under data/private'):
                join_oatly(pdf, src, Path(d) / 'public.json', root,
                           sources=['meny_oatly_barista_500'])

    def test_distinct_product_pages_never_become_one_group_price(self):
        srcs = [t.source for t in TARGETS]
        prices = [analyze_product(src, page(src), captured_at=STAMP,
                                  retrieval='public_http')
                  ['base_price_ore_source_only'] for src in srcs]
        self.assertEqual(prices, [2990, 4990, 5490, 1590])
        self.assertGreater(len(set(prices)), 1)


if __name__ == '__main__':
    unittest.main()
