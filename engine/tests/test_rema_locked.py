"""Offline fixture and private staging contracts for the REMA card parser."""
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nutrivalue.providers.rema_locked import RemaLockedError, parse_html, stage_html, SOURCE_URL

SCHEMA = Path(__file__).resolve().parents[1] / 'analytics/rema_locked_staging.sql'


def card(name='EGG 6PK M/L/XL R', price='26,90 kr', details='402 g - 66,92 kr. kg', before=''):
    return (f'<li><article><div>{before}<span class="sr-only">{price}</span></div>'
            f'<h3>{name}</h3><p class="text-body-xs text-text-secondary">{details}</p>'
            '</article></li>')


def page(cards):
    return f'<html><h1>Låst pris</h1><h2>Kampanjeprodukter</h2><ul>{cards}</ul></html>'


def report(html):
    return dict(source='rema_locked', source_url=SOURCE_URL, final_url=SOURCE_URL,
                fetched_at='2026-10-10T20:19:24+00:00',
                sha256=hashlib.sha256(html).hexdigest(), html_bytes=len(html))


class RemaLockedTests(unittest.TestCase):
    def test_real_card_style_and_multipack(self):
        html = page(card() + card('HVETEMEL 4X2KG','79,60 kr','4x2 kg - 9,95 kr. kg') +
                    card('KYLLINGBULJONG', '17,90 kr', '80 g - 4,48 kr. l',
                         '<span class="sr-only">Før</span><p>19,90</p>'))
        cards = parse_html(html)
        self.assertEqual(len(cards),3)
        self.assertEqual((cards[0].name,cards[0].price_ore,cards[0].package_quantity,
                          cards[0].package_unit,cards[0].unit_price_ore),
                         ('EGG 6PK M/L/XL R',2690,'402','g',6692))
        self.assertEqual((cards[1].pack_count,cards[1].package_quantity,
                          cards[1].price_ore), (4,'2',7960))
        self.assertEqual((cards[2].price_ore,cards[2].unit_price_unit),(1790,'l'))

    def test_fail_closed_invalid_cards(self):
        for bad in (card(price='free'), card(price='0,00 kr'),
                    card(details=''), card().replace('<h3>','<h4>').replace('</h3>','</h4>'),
                    card(price='26,90 kr<span class="sr-only">37,90 kr</span>')):
            with self.subTest(bad=bad), self.assertRaises(RemaLockedError):
                parse_html(page(card() + bad))
        with self.assertRaises(RemaLockedError):
            parse_html(page(''))

    def test_thousand_separator_unit_price(self):
        row = parse_html(page(card('ESTRAGON TØRR TORO', '46,40 kr',
            '20 g - 2 320,00 kr. kg')))[0]
        self.assertEqual((row.package_quantity, row.unit_price_ore), ('20', 232000))

    def test_missing_mass_is_null_and_not_invented(self):
        result = parse_html(page(card('KJØTTKAKESAUS','10,90 kr','242,22 kr. kg')))[0]
        self.assertIsNone(result.package_quantity)
        self.assertIsNone(result.package_unit)
        self.assertIsNone(result.unit_price_ore)

    def test_private_snapshot_report_and_idempotent_staging(self):
        html = page(card()).encode()
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td)/'private.sqlite3'
            with self.assertRaises(RemaLockedError):
                stage_html(html, dict(report(html), sha256='a'*64), db_path, SCHEMA)
            self.assertFalse(db_path.exists())
            stage_html(html, report(html), db_path, SCHEMA)
            stage_html(html, report(html), db_path, SCHEMA)
            with sqlite3.connect(db_path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM rema_locked_cards').fetchone()[0], 1)
                row=db.execute('SELECT price_ore, review_state FROM rema_locked_cards').fetchone()
                self.assertEqual(row,(2690,'source_only'))


if __name__ == '__main__':
    unittest.main()
