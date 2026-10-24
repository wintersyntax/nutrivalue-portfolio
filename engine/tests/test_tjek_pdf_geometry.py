"""TASK-033: synthetic geometry/bundle badge evidence, no network/PDF fixtures."""
from __future__ import annotations

import html
import unittest

from nutrivalue.providers.tjek_pdf_geometry import (
    parse_bbox_pages, match_multibuy_badges, summarize_badge_candidates,
)


def _line(words):
    tokens = "".join(
        '<word xMin="{x0}" yMin="{y0}" xMax="{x1}" yMax="{y1}">{t}</word>'
        .format(x0=x, y0=y, x1=x + w, y1=y + h, t=html.escape(text))
        for text, x, y, w, h in words
    )
    minx = min(x for _, x, _, _, _ in words)
    miny = min(y for _, _, y, _, _ in words)
    maxx = max(x + w for _, x, _, w, _ in words)
    maxy = max(y + h for _, _, y, _, h in words)
    return (f'<line xMin="{minx}" yMin="{miny}" '
            f'xMax="{maxx}" yMax="{maxy}">{tokens}</line>')


def _pages(lines, *, extra_page=""):
    markup = "".join(_line(words) for words in lines)
    return (f'<html><body><doc><page width="800" height="1100">'
            f'{markup}</page>{extra_page}</doc></body></html>').encode()


def _fixture(price=50, *, bundle=True, cents=True, extra_title=False):
    lines = []
    if bundle:
        lines.append([("2", 120, 20, 15, 24), ("for", 143, 20, 42, 24)])
    lines.append([(str(price), 120, 60, 52, 35)])
    if cents:
        lines.append([("00", 180, 78, 35, 30)])
    lines.append([("Coop", 110, 155, 40, 22),
                  ("kyllingpålegg", 155, 155, 125, 22)])
    if extra_title:
        lines.append([("Coop", 85, 160, 40, 20),
                      ("morgenstykker", 130, 160, 130, 20)])
    return parse_bbox_pages(_pages(lines))


def _offer(title="Coop kyllingpålegg", ident="one", price=50):
    return {"id": ident, "catalog_page": 1, "heading": title,
            "pricing": {"price": price, "currency": "NOK"}}


class GeometryEvidenceTests(unittest.TestCase):
    def test_single_title_badge_total_agrees(self):
        got = match_multibuy_badges([_offer()], _fixture())
        self.assertEqual(got[0]["badge_state"], "geometry_total_correlated_review_only")
        self.assertEqual(got[0]["bundle_units_candidate"], 2)
        self.assertTrue(got[0]["source_total_recorroborated"])
        self.assertFalse(got[0]["rank_eligible"])

    def test_large_printed_price_overlaps_for_banner_rectangle(self):
        # Real Coop pages use a very tall '2 for' font box that overlaps
        # the large integer badge. Discarding that overlap loses the offer.
        lines = [
            [("2", 120, 20, 15, 24), ("for", 143, 20, 42, 24)],
            [("50", 120, 35, 52, 81)],
            [("00", 180, 52, 35, 34)],
            [("Coop", 110, 155, 40, 22),
             ("kyllingpålegg", 155, 155, 125, 22)],
        ]
        pages = parse_bbox_pages(_pages(lines))
        rows = match_multibuy_badges([_offer()], pages)
        self.assertEqual(rows[0]["badge_state"],
                         "geometry_total_correlated_review_only")

    def test_source_total_disagrees(self):
        got = match_multibuy_badges([_offer(price=45)], _fixture())
        self.assertEqual(got[0]["badge_state"], "geometry_without_total_review")
        self.assertIsNone(got[0]["bundle_units_candidate"])

    def test_without_cents_printed_total_not_correlated(self):
        got = match_multibuy_badges([_offer()], _fixture(cents=False))
        self.assertEqual(got[0]["badge_state"], "geometry_without_total_review")

    def test_percentage_cannot_be_added_to_source_amount(self):
        got = match_multibuy_badges([_offer()], _fixture(bundle=False))
        self.assertEqual(got[0]["badge_state"], "no_unique_geometry")
        self.assertFalse(got[0]["source_total_recorroborated"])

    def test_two_competing_products_no_badge_assignment(self):
        rows = [_offer(), _offer("Coop morgenstykker", "two")]
        got = match_multibuy_badges(rows, _fixture(extra_title=True))
        self.assertTrue(all(row["badge_state"] == "no_unique_geometry" for row in got))

    def test_missing_heading_not_rescued_by_nearby_generic_title(self):
        got = match_multibuy_badges([_offer(title="Other item")], _fixture())
        self.assertEqual(got[0]["badge_state"], "no_unique_geometry")

    def test_original_page_must_match(self):
        with self.assertRaises(ValueError):
            match_multibuy_badges([_offer() | {"catalog_page": 2}], _fixture())

    def test_blank_pdf_page_preserved(self):
        other = '<page width="800" height="1100"></page>'
        pages = parse_bbox_pages(_pages([[
            ("Product", 30, 40, 80, 20)]], extra_page=other))
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(pages[1].lines), 0)

    def test_duplicate_identity_fails_closed(self):
        with self.assertRaises(ValueError):
            match_multibuy_badges([_offer(), _offer()], _fixture())

    def test_malformed_or_out_of_bounds_geometry_rejected(self):
        with self.assertRaises(ValueError):
            parse_bbox_pages(b'<html><page width="1"')
        with self.assertRaises(ValueError):
            parse_bbox_pages(_pages([[("outside", 900, 0, 70, 20)]]))

    def test_only_aggregate_output_contains_no_item_price(self):
        got = match_multibuy_badges([_offer()], _fixture())
        counts = summarize_badge_candidates(got)
        self.assertEqual(counts["bundle_total_geometry_correlated_review_only"], 1)
        self.assertEqual(counts["price_became_rank_eligible"], 0)
        self.assertNotIn("kylling", str(counts))
        self.assertNotIn("50", str(counts))


if __name__ == "__main__":
    unittest.main()
