"""TASK-033: synthetic title/page joins; no private PDF or Tjek network."""
import unittest

from nutrivalue.providers.tjek_pdf_link import match_page_titles, count_states, physical_text_pages


def offer(heading, page, oid="one"):
    return {"id": oid, "catalog_id": "fake_extra", "catalog_page": page,
            "heading": heading, "pricing": {"price": 45}}


class PdfTitleJoinTests(unittest.TestCase):
    def test_blank_middle_page_keeps_source_pdf_page_number(self):
        pages = physical_text_pages("Page one\f\fTine revet lettost\f")
        self.assertEqual(len(pages), 3)
        self.assertFalse(pages[1].strip())
        result = match_page_titles([offer("Tine revet lettost", 3)], pages)
        self.assertEqual(result[0]["title_match_state"], "page_title_correlated")
        mistaken = match_page_titles([offer("Tine revet lettost", 2)], pages)
        self.assertEqual(mistaken[0]["title_match_state"], "page_title_unresolved")

    def test_final_blank_pdf_page_not_removed(self):
        pages = physical_text_pages("PDF title\fPDF body\f\f")
        self.assertEqual(len(pages), 3)
        self.assertEqual(sum(bool(p.strip()) for p in pages), 2)

    def test_terminal_formfeed_is_not_a_thirtieth_page(self):
        pages = physical_text_pages("Physical page 1\fPhysical page 2\f")
        self.assertEqual(len(pages), 2)

    def test_empty_pdf_text_rejected(self):
        with self.assertRaises(ValueError):
            physical_text_pages("\f\f")

    def test_exact_same_page_title_correlated_no_price(self):
        records = match_page_titles(
            [offer("Tine revet lettost", 2)],
            ["Other flyer page", "Tine revet lettost  2 for 45 kr"])
        self.assertEqual(records[0]["title_match_state"], "page_title_correlated")
        self.assertEqual(records[0]["bundle_condition_link"], "unresolved")
        self.assertEqual(records[0]["price_badge_link"], "unresolved")
        self.assertFalse(records[0]["rank_eligible"])

    def test_missing_title_on_declared_page_is_not_rescued_by_other_page(self):
        records = match_page_titles([offer("Libero up & go str 5", 2)],
                                    ["Libero up & go str 5", "Other products"])
        self.assertEqual(records[0]["title_match_state"], "page_title_unresolved")

    def test_split_layout_title_is_review_only(self):
        records = match_page_titles(
            [offer("Coop fullkornpasta", 1)],
            ["Coop 2 for 35 kr ... fullkornpasta"])
        self.assertEqual(records[0]["title_match_state"], "page_tokens_only_review")

    def test_no_cross_page_or_disconnected_token_join(self):
        records = match_page_titles(
            [offer("Freia pizza", 1)],
            ["Freia waffles. Pizza from Coop"])
        self.assertEqual(records[0]["title_match_state"], "page_tokens_only_review")
        self.assertEqual(records[0]["member_discount_link"], "unresolved")

    def test_page_outside_pdf_rejected(self):
        with self.assertRaises(ValueError):
            match_page_titles([offer("Tine yoghurt", 3)],
                              ["Page 1", "Page 2"])
        with self.assertRaises(ValueError):
            match_page_titles([offer("Tine yoghurt", True)], ["Page 1"])

    def test_duplicate_offer_id_rejected(self):
        with self.assertRaises(ValueError):
            match_page_titles([offer("Milk", 1), offer("Cream", 1)], ["Milk Cream"])

    def test_short_heading_unresolved(self):
        records = match_page_titles([offer("YT", 1)], ["YT YT YT"])
        self.assertEqual(records[0]["title_match_state"], "unresolved_short_heading")

    def test_unicode_punctuation_title(self):
        records = match_page_titles(
            [offer("LEGO® Botanicals", 1)],
            ["LEGO Botanicals  -50% member"])
        self.assertEqual(records[0]["title_match_state"], "page_title_correlated")

    def test_aggregated_counts_not_private_values(self):
        records = match_page_titles(
            [offer("Libero up & go str 5", 1, "private-1"),
             offer("Coop fullkornpasta", 2, "private-2")],
            ["Libero up & go str 5", "Coop 2 for 35 fullkornpasta"])
        result = count_states(records)
        self.assertEqual(result["api_offers"], 2)
        self.assertEqual(result["direct_title_page"], 1)
        self.assertEqual(result["tokens_review_only"], 1)
        self.assertEqual(result["unresolved_short_heading"], 0)
        self.assertEqual(result["unresolved_title_not_found"], 0)
        self.assertEqual(result["price_badges_attached"], 0)
        self.assertEqual(result["pdf_new_offers_recovered"], 0)
        self.assertNotIn("private", repr(result))


if __name__ == "__main__":
    unittest.main()
