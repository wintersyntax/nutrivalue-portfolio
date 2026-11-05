"""Dependency-free guards for one-shot MENY product browser diagnostics."""
import unittest

from scripts.probe_meny_oatly_render import (
    PRIVATE_DIR, SOURCE, URL, exact_page_url, page_signals,
)


class MenyOatlyRenderTests(unittest.TestCase):
    def test_pinned_exact_public_product_navigation(self):
        self.assertEqual(SOURCE, "meny_oatly_barista_500")
        self.assertTrue(exact_page_url(URL))
        self.assertTrue(exact_page_url(URL + "/"))
        for altered in (
                "http://" + URL.removeprefix("https://"),
                URL + "?foo=bar",
                URL + "#fragment",
                URL.rsplit("/", 1)[0] + "/other",
                URL.replace("meny.no", "not-meny.no"),
                URL.replace("https://", "https://" + "user" + ":" + "pw" + "@"),
                "https://meny.no.evil.example" + URL.split("meny.no")[1]):
            with self.subTest(url=altered):
                self.assertFalse(exact_page_url(altered))

    def test_page_title_with_price_but_only_nav_does_not_verify_price(self):
        result = page_signals(
            "Kjøp Havredrikk. Barista Edition 0.5l Oatly - 29,90 kr | MENY",
            "MENY Tjenester Ledige stillinger Kundeservice")
        self.assertTrue(result["title_mentions_29_90"])
        self.assertTrue(result["title_mentions_oatly"])
        self.assertFalse(result["visible_mentions_29_90"])
        self.assertFalse(result["visible_mentions_campaign"])
        self.assertFalse(result["verified_price"])
        self.assertEqual(result["product_campaign_price_join"], "not_attempted")

    def test_visible_text_flags_are_diagnostics_not_store_price(self):
        result = page_signals("Oatly", (
            "Oatly Barista Edition 0,5 l 29 90 kr 59,80 kr/l. "
            "Plukk & miks Oatly produkter 3 for 2"))
        self.assertTrue(result["visible_mentions_oatly"])
        self.assertTrue(result["visible_mentions_29_90"])
        self.assertTrue(result["visible_mentions_campaign"])
        self.assertTrue(result["visible_mentions_price_unit"])
        self.assertFalse(result["verified_price"])
        self.assertFalse(result["store_selected"])

    def test_loading_markers_do_not_become_priced_cards(self):
        result = page_signals(
            "Barista Edition Oatly - 29,90 kr | MENY",
            "Laster produkter Laster kategorier MENY")
        self.assertTrue(result["visible_mentions_loading"])
        self.assertFalse(result["visible_mentions_29_90"])
        self.assertFalse(result["visible_mentions_campaign"])
        self.assertFalse(result["verified_price"])

    def test_capture_path_under_private_evidence_area(self):
        self.assertIn("data/private/render-probes", PRIVATE_DIR.as_posix())
        self.assertEqual(PRIVATE_DIR.name, "meny-oatly-barista")


if __name__ == "__main__":
    unittest.main()
