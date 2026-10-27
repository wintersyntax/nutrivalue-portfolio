"""Synthetic, offline-only tests for KIWI Prislås split-price cards."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nutrivalue.providers.kiwi_locked import KiwiLockedError, SOURCE_URL, parse_html, stage_html

SCHEMA = Path(__file__).resolve().parents[1] / 'analytics/kiwi_locked_staging.sql'


def fixture(name='SMOOTHIE BANAN / JORDBÆR', desc='90 g, Lillego', unit='Pr. kg 104,44',
            main='9', fraction='40', pant=''):
    return f'''<html data-chain="kiwi"><h1>Prislås hos KIWI</h1>
    <section><header><h3>Baby og barnemat</h3></header>
    <li><article class="campaignCard_card__A">
      <div><h3 class="campaignCard_productName__B">{name}</h3>
      <p class="campaignCard_description__C">{desc}</p>
      <p class="campaignCard_campaignDescription__D">{unit}</p>
      <p class="campaignCard_regularPrice__E">
       <span class="campaignCard_mainPrice__F">{main}</span>
       <span class="campaignCard_decimalsContainer__Q">
        <span class="campaignCard_decimals__G">{fraction}</span>
        {f'<span class="campaignCard_recyclable__X">+ pant</span>' if pant else ''}
       </span></p></div></article></li></section>
    <script>var fake = '999,99 kr';</script></html>'''


def report(payload):
    return dict(source='kiwi_locked', source_url=SOURCE_URL, final_url=SOURCE_URL,
                fetched_at='2026-10-10T20:19:24+00:00', html_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest())


class KiwiLockedTests(unittest.TestCase):
    def test_split_price_and_bounded_article(self):
        row, = parse_html(fixture())
        self.assertEqual((row.name, row.price_ore, row.source_unit_price_ore),
                         ('SMOOTHIE BANAN / JORDBÆR', 940, 10444))
        self.assertEqual((row.package_quantity, row.package_unit, row.unit_price_check),
                         ('90', 'g', 'match'))
        self.assertFalse(row.deposit_applies)

    def test_recyclable_excludes_deposit_and_multipack(self):
        card, = parse_html(fixture(name='RADLER LEMON 4PK', desc='4x0,33 liter, Hansa',
                                   unit='Pr. liter 30,00', main='39', fraction='60', pant=True))
        self.assertEqual((card.price_ore, card.pack_count, card.package_quantity),
                         (3960, 4, '0.33'))
        self.assertEqual(card.unit_price_check, 'match')
        self.assertTrue(card.deposit_applies)

    def test_mismatching_source_unit_price_is_flagged_not_corrected(self):
        card, = parse_html(fixture(name='FULLKORNSGRØT', desc='450 g, Nestle',
                                   unit='Pr. kg 34,10', main='69', fraction='90'))
        self.assertEqual(card.price_ore, 6990)
        self.assertEqual(card.source_unit_price_ore, 3410)
        self.assertEqual(card.unit_price_check, 'mismatch')

    def test_absent_mass_or_unit_rate_remain_unknown(self):
        card, = parse_html(fixture(name='BRUN SAUS', desc='Toro', unit='Pr. liter 27,80',
                                   main='13', fraction='90'))
        self.assertIsNone(card.package_quantity)
        self.assertEqual(card.unit_price_check, 'unchecked')
        card, = parse_html(fixture(name='RAPSOLJE', desc='1 liter, Eldorado', unit='Pr.liter',
                                   main='29', fraction='90'))
        self.assertIsNone(card.source_unit_price_ore)
        self.assertEqual(card.source_unit_price_unit, 'liter')

    def test_fail_closed_wrong_page_missing_price_and_cross_card(self):
        for source in [fixture().replace('data-chain="kiwi"','data-chain="other"'),
                       fixture().replace('Prislås hos KIWI','OTHER'),
                       fixture().replace('campaignCard_decimals__G','broken_decimals'),
                       fixture().replace('campaignCard_mainPrice__F','broken_price'),
                       fixture().replace('campaignCard_productName__B','broken_name'),
                       fixture(main='0', fraction='00')]:
            with self.subTest(source=source[:75]):
                with self.assertRaises(KiwiLockedError):
                    parse_html(source)

    def test_no_cross_card_price_association(self):
        normal = fixture()
        broken = normal.replace('campaignCard_decimals__G', 'broken_decimals')
        html = normal.replace('</section>', broken.split('<li>')[1].split('</li>')[0].join(['<li>','</li>']) + '</section>')
        with self.assertRaises(KiwiLockedError):
            parse_html(html)

    def test_report_validation_no_mutation(self):
        content = fixture().encode()
        with tempfile.TemporaryDirectory() as dirname:
            db = Path(dirname) / 'private' / 'kiwi.sqlite3'
            for change in ({'source': 'rema_locked'}, {'sha256': '0'*64},
                           {'fetched_at':'2026-10-10T20:19:24'}, {'html_bytes':0}):
                with self.subTest(change=change):
                    with self.assertRaises(KiwiLockedError):
                        stage_html(content, {**report(content), **change}, db, SCHEMA)
                    self.assertFalse(db.exists())

    def test_idempotent_private_staging_and_unchanged_source(self):
        content = fixture().encode()
        with tempfile.TemporaryDirectory() as dirname:
            db_path = Path(dirname) / 'private' / 'kiwi.sqlite3'
            for _ in range(2):
                count, sha = stage_html(content, report(content), db_path, SCHEMA)
                self.assertEqual(count, 1)
                self.assertEqual(sha, hashlib.sha256(content).hexdigest())
            with sqlite3.connect(db_path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM kiwi_locked_snapshots').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM kiwi_locked_cards').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT review_state FROM kiwi_locked_cards').fetchone()[0], 'source_only')
                self.assertEqual(db.execute('SELECT original_html FROM kiwi_locked_snapshots').fetchone()[0], content.decode())

    def test_invalid_json_type_fails_without_db(self):
        content = fixture().encode()
        with tempfile.TemporaryDirectory() as dirname:
            db = Path(dirname) / 'private.sqlite3'
            with self.assertRaises(KiwiLockedError):
                stage_html(content, [], db, SCHEMA)
            self.assertFalse(db.exists())

    def test_oversized_invalid_utf8_and_missing_cards(self):
        content = fixture().encode()
        with tempfile.TemporaryDirectory() as dirname:
            db = Path(dirname) / 'private.sqlite3'
            for raw in (b'\xff\xfe\xfd', b'X' * 2_000_001,
                        b'<html data-chain="kiwi"><h1>Prisl\xc3\xa5s hos KIWI</h1><script>campaignCard_card__</script></html>'):
                with self.subTest(length=len(raw)):
                    with self.assertRaises(KiwiLockedError):
                        stage_html(raw, report(raw), db, SCHEMA)
                    self.assertFalse(db.exists())

    def test_stk_and_pack_rate_arithmetic(self):
        row, = parse_html(fixture(name='BLEIEPOSER', desc='3x15 stk., Lillego',
                                  unit='Pr. stk. 0,21', main='9', fraction='40'))
        self.assertEqual((row.package_quantity, row.pack_count), ('15', 3))
        self.assertEqual(row.unit_price_check, 'match')
        row, = parse_html(fixture(name='PAPIR', desc='8-pk., Vilje',
                                  unit='Pr. stk. 2,49', main='19', fraction='90'))
        self.assertEqual((row.package_quantity, row.package_unit), ('8', 'pk'))
        self.assertEqual(row.unit_price_check, 'unchecked')

    def test_structural_duplicate_fields_rejected(self):
        dup = fixture().replace('<span class="campaignCard_mainPrice__F">9</span>',
                                '<span class="campaignCard_mainPrice__F">9</span><span class="campaignCard_mainPrice__Z">50</span>')
        with self.assertRaises(KiwiLockedError):
            parse_html(dup)


if __name__ == '__main__':
    unittest.main()
