"""TASK-033: synthetic member badge corroboration, no PDF files or HTTP."""
from __future__ import annotations

import unittest

from nutrivalue.providers.tjek_pdf_geometry import Box, Page, _ore
from nutrivalue.providers.tjek_pdf_member import (
    correlate_member_badges, member_badge_summary,
)


def _offer(source=77.90, desc="Ikke medlem: 77,90 Spar kr 38,95",
           name="Demo diaper pack 5", oid="x"):
    return {
        "id": oid, "heading": name, "catalog_page": 1,
        "description": desc,
        "pricing": {"currency": "NOK", "price": source, "pre_price": None},
    }


def _page(title="Demo diaper pack 5", *, member_big="38", cents="95",
          nonmember="Ikke medlem: 77,90", saving="Spar kr 38,95"):
    label = Box(title, 86, 1025, 205, 1043)
    lines = (
        Box(saving, 90, 921, 170, 940),
        label,
        Box(nonmember, 100, 1059, 190, 1071),
    )
    words = (Box(member_big, 88, 933, 154, 1019),
             Box(cents, 155, 947, 176, 974))
    return Page(800, 1133, lines, words)


class MemberGeometryTests(unittest.TestCase):
    def test_printed_norwegian_decimal_comma_is_exact_ore(self):
        self.assertEqual(_ore("77,90"), 7790)
        self.assertEqual(_ore("38,95"), 3895)
        self.assertEqual(_ore("38.95"), 3895)
        self.assertIsNone(_ore("38,955"))
        self.assertIsNone(_ore("not-a-price"))
        self.assertIsNone(_ore("NaN"))

    def test_exact_member_arithmetic_and_pdf_badge(self):
        rows = correlate_member_badges([_offer()], [_page()])
        self.assertEqual(rows[0]["state"], "pdf_member_arithmetic_correlated_review_only")
        self.assertEqual(rows[0]["member_amount_review_only_ore"], 3895)
        self.assertFalse(rows[0]["rank_eligible"])
        counts = member_badge_summary(rows)
        self.assertEqual(counts["member_badge_correlated_review_only"], 1)
        self.assertEqual(counts["membership_checkout_verified"], 0)

    def test_whole_kr_member_badge_without_cents(self):
        page = Page(800, 1100, (
            Box("Ikke-medlem: 199", 516, 405, 623, 421),
            Box("Spar: 70", 545, 420, 594, 436),
            Box("Demo flower set", 452, 465, 630, 491),
        ), (Box("129", 507, 303, 637, 415),))
        offer = _offer(source=199, desc="Ikke-medlem: 199 Spar: 70",
                       name="Demo flower set")
        rows = correlate_member_badges([offer], [page])
        self.assertEqual(rows[0]["member_amount_review_only_ore"], 12900)

    def test_one_exact_parent_title_outranks_two_variant_prefixes(self):
        # A flyer may list a single bundle/family headline plus two variants.
        page = Page(800, 1100, (
            Box("Demo flower set", 452, 465, 630, 491),
            Box("Demo flower set variant A", 452, 492, 707, 509),
            Box("Demo flower set variant B", 452, 509, 749, 526),
            Box("Ikke-medlem: 199", 516, 405, 623, 421),
            Box("Spar: 70", 545, 420, 594, 436),
        ), (Box("129", 507, 303, 637, 415),))
        offer = _offer(source=199, desc="Ikke-medlem: 199 Spar: 70",
                       name="Demo flower set")
        result = correlate_member_badges([offer], [page])
        self.assertEqual(result[0]["state"],
                         "pdf_member_arithmetic_correlated_review_only")
        self.assertEqual(result[0]["member_amount_review_only_ore"], 12900)
        self.assertFalse(result[0]["rank_eligible"])

    def test_repeated_exact_parent_titles_remain_ambiguous(self):
        original = _page()
        repeated = Page(800, 1133, original.lines + (
            Box("Demo diaper pack 5", 300, 1025, 420, 1043),
        ), original.words)
        result = correlate_member_badges([_offer()], [repeated])
        self.assertEqual(result[0]["state"], "member_candidate_unmatched_pdf")

    def test_two_prefix_titles_without_exact_parent_remain_ambiguous(self):
        original = _page()
        prefix_only = Page(800, 1133, (
            Box("Demo diaper pack 5 first variant", 86, 1025, 240, 1043),
            Box("Demo diaper pack 5 second variant", 270, 1025, 440, 1043),
            *original.lines[:1], *original.lines[2:],
        ), original.words)
        result = correlate_member_badges([_offer()], [prefix_only])
        self.assertEqual(result[0]["state"], "member_candidate_unmatched_pdf")

    def test_membership_never_subtracted_twice_when_source_already_discounted(self):
        offer = _offer(source=38.95, desc="Førpris 77,90 Spar kr 38,95")
        rows = correlate_member_badges([offer], [_page()])
        self.assertEqual(rows[0]["state"], "no_explicit_candidate")
        self.assertIsNone(rows[0]["member_amount_review_only_ore"])

    def test_source_nonmember_price_must_match_text(self):
        rows = correlate_member_badges([_offer(source=70)], [_page()])
        self.assertEqual(rows[0]["state"], "no_explicit_candidate")

    def test_wrong_printed_price_is_not_relabelled(self):
        rows = correlate_member_badges([_offer()], [_page(member_big="39")])
        self.assertEqual(rows[0]["state"], "member_candidate_unmatched_pdf")

    def test_wrong_pdf_saving_refuses_evidence(self):
        rows = correlate_member_badges([_offer()], [_page(saving="Spar kr 15,45")])
        self.assertEqual(rows[0]["state"], "member_candidate_unmatched_pdf")

    def test_wrong_pdf_nonmember_refuses_evidence(self):
        rows = correlate_member_badges([_offer()], [_page(nonmember="Ikke medlem: 70,00")])
        self.assertEqual(rows[0]["state"], "member_candidate_unmatched_pdf")

    def test_duplicate_discount_line_is_ambiguous(self):
        original = _page()
        duplicates = original.lines + (Box("Spar kr 38,95", 110, 900, 190, 920),)
        page = Page(800, 1133, duplicates, original.words)
        rows = correlate_member_badges([_offer()], [page])
        self.assertEqual(rows[0]["state"], "member_candidate_unmatched_pdf")

    def test_wrong_page_and_missing_title(self):
        with self.assertRaises(ValueError):
            correlate_member_badges([_offer() | {"catalog_page": 2}], [_page()])
        rows = correlate_member_badges([_offer()], [_page(title="Completely other item")])
        self.assertEqual(rows[0]["state"], "member_candidate_unmatched_pdf")

    def test_duplicate_offer_id_rejected(self):
        with self.assertRaises(ValueError):
            correlate_member_badges([_offer(), _offer()], [_page()])

    def test_group_percent_not_an_additional_discount(self):
        row = _offer(source=30, desc="25 % rabatt på utvalgte varer")
        result = correlate_member_badges([row], [_page()])
        self.assertEqual(result[0]["state"], "no_explicit_candidate")
        self.assertEqual(member_badge_summary(result)["member_price_rank_eligible"], 0)


if __name__ == "__main__":
    unittest.main()
