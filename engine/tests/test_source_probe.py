"""Synthetic offline validation of free read-only public source probes."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nutrivalue.providers.source_probe import (
    MAX_HTML_BYTES, SOURCES, SourceProbeError, analyze_html,
    fetch_once, save_private_snapshot, validate_url,
)

def sample(content):
    return ("<!doctype html><html><head><title>Test groceries</title>"
            "<script type='application/ld+json'>{\"price\":\"0.01\","
            "\"name\":\"NOT A VISIBLE PRODUCT\"}</script></head><body>"
            + content + "</body></html>").encode()

class SourceProbeTests(unittest.TestCase):
    def test_pin_all_published_sources_and_reject_outside_url(self):
        for name, source in SOURCES.items():
            with self.subTest(name=name):
                validate_url(source, source.url)
                for bad in ("http://" + source.allowed_hosts[0] + "/",
                            "https://evil.example" + source.url.split(".no")[-1],
                            source.url + "?unexpected=1",
                            source.url + "#secret",
                            "https://user:pass@" + source.allowed_hosts[0]
                            + source.url.split(".no")[-1]):
                    with self.assertRaises(SourceProbeError):
                        validate_url(source, bad)

    def test_rema_html_marks_visible_and_skips_jsonld_fake_price(self):
        raw = sample("<h1>Låst pris</h1><h2>Kampanjeprodukter</h2>"
                     "<h3>KYLLINGKJØTTBOLLER</h3><p>59,90 kr</p>"
                     "<p>600 g - 99,83 kr. kg</p>")
        result = analyze_html("rema_locked", raw, fetched_at="2026-10-10T15:00:00Z")
        self.assertEqual(result["marker_phrases_found"],
                         ["Låst pris", "Kampanjeprodukter"])
        self.assertEqual(result["evidence_state"], "visible_text_needs_review")
        self.assertEqual(result["product_heading_sample"],
                         ["KYLLINGKJØTTBOLLER"])
        self.assertEqual(result["currency_text_mentions"], 2)
        self.assertEqual(result["jsonld_script_count"], 1)
        self.assertNotIn("NOT A VISIBLE PRODUCT", json.dumps(result))
        self.assertEqual(result["price_verification"], "none")
        self.assertEqual(result["ean_verification"], "none")
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())

    def test_kiwi_price_badge_not_fabricated_as_package_price(self):
        raw = sample("<h1>Prislås</h1><h3>SMOOTHIE BANAN</h3>"
                     "<p>90 g, Lillego</p><p>Pr. kg 104,44</p>"
                     "<span>9</span><span>40</span>")
        result = analyze_html("kiwi_locked", raw)
        self.assertEqual(result["evidence_state"], "visible_text_needs_review")
        self.assertEqual(result["currency_text_mentions"], 0)
        self.assertEqual(result["unit_price_text_mentions"], 1)
        self.assertEqual(result["price_verification"], "none")

    def test_sesum_week_and_product_are_only_source_evidence(self):
        fixtures = {
            "sesum_weekly": "<h1>Ukens avis</h1><h2>Uke 41</h2>"
                            "<h3>Kyllingfilet</h3><p>59,90 kr</p>",
            "sesum_product": "<h1>Cottage Cheese Mager</h1>"
                             "<h2>Priser per kjede</h2><p>KIWI 26,90 kr</p>",
        }
        for name, body in fixtures.items():
            with self.subTest(name=name):
                result = analyze_html(name, sample(body))
                self.assertEqual(result["evidence_state"],
                                 "visible_text_needs_review")
                self.assertEqual(result["price_verification"], "none")

    def test_coop_weekly_skeleton_never_becomes_a_product_offer(self):
        fixtures = {
            "coop_prix_weekly": "<h1>Coop Prix</h1><h2>Ukens tilbud</h2>"
                                "<p>Les ukens kundeavis</p>",
            "coop_extra_weekly": "<h1>Extra</h1><h2>Ukens utvalgte tilbud fra kundeavis</h2>"
                                 "<p>Les ukens kundeavis her</p>",
        }
        for name, body in fixtures.items():
            with self.subTest(name=name):
                raw = sample(body + "<h3>Taco</h3><h3>Kontakt oss</h3>"
                             "<article><span>1</span><span>for</span><span>-1%</span>"
                             "<h3>▆▆▆▆▆ ▆▆▆▆▆</h3></article>")
                result = analyze_html(name, raw)
                self.assertEqual(result["evidence_state"], "structure_only_needs_review")
                self.assertEqual(result["product_heading_sample"], [])
                self.assertEqual(result["coop_non_placeholder_h3_sample"], [])
                self.assertEqual(result["coop_article_count"], 1)
                self.assertEqual(result["coop_placeholder_article_h3_count"], 1)
                self.assertTrue(result["coop_requires_render_review"])
                self.assertEqual(result["currency_text_mentions"], 0)
                self.assertEqual(result["price_verification"], "none")
                self.assertEqual(result["ean_verification"], "none")
                self.assertNotIn("NOT A VISIBLE PRODUCT", json.dumps(result))

    def test_coop_visible_named_heading_stays_source_only_not_a_price_import(self):
        raw = sample("<h1>Coop Prix</h1><h2>Ukens tilbud</h2>"
                     "<p>Les ukens kundeavis</p>"
                     "<h3>Les ukens kundeavis</h3>"
                     "<article><h3>Coop Kjøttdeig 14% 400g</h3>"
                     "<p>73,90 kr</p></article>")
        result = analyze_html("coop_prix_weekly", raw)
        self.assertEqual(result["evidence_state"], "visible_text_needs_review")
        self.assertEqual(result["coop_non_placeholder_h3_sample"],
                         ["Coop Kjøttdeig 14% 400g"])
        self.assertFalse(result["coop_requires_render_review"])
        self.assertEqual(result["coop_article_count"], 1)
        self.assertEqual(result["coop_placeholder_article_h3_count"], 0)
        self.assertEqual(result["currency_text_mentions"], 1)
        self.assertEqual(result["price_verification"], "none")
        self.assertEqual(result["ean_verification"], "none")

    def test_coop_real_snapshot_pattern_nav_titles_cannot_hide_skeletons(self):
        for name, marker in (
            ("coop_prix_weekly", "Ukens tilbud"),
            ("coop_extra_weekly", "Ukens utvalgte tilbud fra kundeavis"),
        ):
            with self.subTest(source=name):
                # Mirrors structure of privately saved Coop Prix/Extra HTML:
                # three skeleton weekly <article>s, unrelated recipe/footer H3s.
                raw = sample(
                    f"<h2>{marker}</h2><h3>Les ukens kundeavis</h3>"
                    "<h3>Taco</h3><h3>Bli medlem</h3>"
                    + 3 * ("<article><span>1 for -1%</span>"
                           "<h3>▆▆▆▆▆▆▆▆▆▆▆ ▆▆▆▆▆▆▆▆▆▆▆▆</h3>"
                           "</article>")
                )
                result = analyze_html(name, raw)
                self.assertEqual(result["coop_article_count"], 3)
                self.assertEqual(result["coop_placeholder_article_h3_count"], 3)
                self.assertTrue(result["coop_requires_render_review"])
                self.assertEqual(result["evidence_state"], "structure_only_needs_review")
                self.assertEqual(result["product_heading_sample"], [])
                self.assertEqual(result["currency_text_mentions"], 0)
                self.assertEqual(result["price_verification"], "none")

    def test_coop_mixed_visible_and_skeleton_articles_is_still_incomplete(self):
        raw = sample(
            "<h2>Ukens tilbud</h2>"
            "<article><h3>Chicken 400 g</h3><p>59,90 kr</p></article>"
            "<article><h3>▆▆▆▆▆▆</h3><span>1 for -1%</span></article>"
        )
        result = analyze_html("coop_prix_weekly", raw)
        self.assertEqual(result["product_heading_sample"], ["Chicken 400 g"])
        self.assertEqual(result["coop_article_count"], 2)
        self.assertEqual(result["coop_placeholder_article_h3_count"], 1)
        self.assertTrue(result["coop_requires_render_review"])
        self.assertEqual(result["evidence_state"], "structure_only_needs_review")
        self.assertEqual(result["price_verification"], "none")

    def test_coop_shared_host_pacing_prevents_immediate_second_chain_get(self):
        from nutrivalue.providers.source_probe import fetch_spaced
        with tempfile.TemporaryDirectory() as temp, \
             patch("nutrivalue.providers.source_probe.fetch_once",
                   return_value=(sample("<h1>Coop Prix</h1>"),
                                 SOURCES["coop_prix_weekly"].url)) as request:
            root = Path(temp)
            fetch_spaced("coop_prix_weekly", root, now_epoch=1_000_000)
            with self.assertRaisesRegex(SourceProbeError, "deferred until"):
                fetch_spaced("coop_extra_weekly", root, now_epoch=1_000_001)
            self.assertEqual(request.call_count, 1)
            state = json.loads((root / ".host-pacing/www.coop.no.json").read_text())
            self.assertEqual(state["not_before_epoch"], 1_000_060)

    def test_meny_campaigns_postcode_and_loader_are_only_structure_evidence(self):
        raw = sample("<h1>Kampanjer</h1><h2>Bla i ukens kundeavis</h2>"
                     "<label>Skriv inn postnummeret ditt</label>"
                     "<h2>Alle tilbud</h2><p>Laster produkter</p>"
                     "<h3>Karbonadedeig</h3><p>69,90 kr</p>")
        report = analyze_html("meny_campaigns", raw)
        self.assertEqual(report["marker_phrases_found"],
                         ["Bla i ukens kundeavis", "Alle tilbud"])
        self.assertEqual(report["meny_loader_phrases_found"], ["Laster produkter"])
        self.assertTrue(report["meny_postcode_prompt_visible"])
        self.assertTrue(report["meny_requires_render_review"])
        self.assertEqual(report["evidence_state"], "structure_only_needs_review")
        self.assertEqual(report["product_heading_sample"], [])
        self.assertEqual(report["currency_text_mentions"], 1)
        self.assertEqual(report["meny_local_store_price_verification"], "none")
        self.assertEqual(report["price_verification"], "none")
        self.assertEqual(report["ean_verification"], "none")

    def test_meny_weekly_teaser_and_nav_not_mistaken_for_offer_card(self):
        raw = sample("<h1>Ukens tilbud</h1><p>Laster produkter</p>"
                     "<p>Laster kategorier</p><h3>Evergood kaffe</h3>"
                     "<h3>Kontakt oss</h3><p>39,90 kr</p>")
        report = analyze_html("meny_weekly", raw)
        self.assertEqual(report["meny_loader_phrases_found"],
                         ["Laster produkter", "Laster kategorier"])
        self.assertEqual(report["meny_article_h3_count"], 0)
        self.assertEqual(report["product_heading_sample"], [])
        self.assertEqual(report["evidence_state"], "structure_only_needs_review")
        self.assertTrue(report["meny_requires_render_review"])
        self.assertEqual(report["currency_text_mentions"], 1)
        self.assertEqual(report["price_verification"], "none")

    def test_meny_weekly_article_is_still_unverified_source_structure(self):
        raw = sample("<h1>Ukens tilbud</h1><h3>Kontakt oss</h3>"
                     "<article><h3>Karbonadedeig</h3>"
                     "<p>400g, 69,90 kr</p></article>")
        report = analyze_html("meny_weekly", raw)
        self.assertEqual(report["product_heading_sample"], ["Karbonadedeig"])
        self.assertEqual(report["meny_article_h3_count"], 1)
        self.assertFalse(report["meny_requires_render_review"])
        self.assertEqual(report["evidence_state"], "visible_text_needs_review")
        self.assertEqual(report["meny_local_store_price_verification"], "none")
        self.assertEqual(report["ean_verification"], "none")
        self.assertEqual(report["price_verification"], "none")

    def test_meny_campaigns_requires_local_store_review_even_with_article(self):
        raw = sample("<h1>Bla i ukens kundeavis</h1>"
                     "<h2>Alle tilbud</h2>"
                     "<article><h3>Kyllingfilet 650g</h3>"
                     "<p>79,90 kr</p></article>")
        report = analyze_html("meny_campaigns", raw)
        self.assertEqual(report["meny_article_h3_count"], 1)
        self.assertTrue(report["meny_requires_render_review"])
        self.assertEqual(report["evidence_state"], "structure_only_needs_review")
        self.assertEqual(report["meny_local_store_price_verification"], "none")

    def test_meny_hidden_loader_inside_script_does_not_trigger_readiness(self):
        raw = sample("<h1>Ukens tilbud</h1>"
                     "<script>Laster produkter</script>"
                     "<article><h3>Kyllingfilet</h3><p>49,90 kr</p></article>")
        report = analyze_html("meny_weekly", raw)
        self.assertEqual(report["meny_loader_phrases_found"], [])
        self.assertFalse(report["meny_requires_render_review"])
        self.assertEqual(report["price_verification"], "none")

    def test_meny_shared_host_pacing_applies_to_both_pinned_pages(self):
        from nutrivalue.providers.source_probe import fetch_spaced
        with tempfile.TemporaryDirectory() as temp, \
             patch("nutrivalue.providers.source_probe.fetch_once",
                   return_value=(sample("<h1>Ukens tilbud</h1>"),
                                 SOURCES["meny_weekly"].url)) as request:
            root = Path(temp)
            fetch_spaced("meny_weekly", root, now_epoch=1_000_000)
            with self.assertRaisesRegex(SourceProbeError, "deferred until"):
                fetch_spaced("meny_campaigns", root, now_epoch=1_000_001)
            self.assertEqual(request.call_count, 1)
            state = json.loads((root / ".host-pacing/meny.no.json").read_text())
            self.assertEqual(state["not_before_epoch"], 1_000_060)

    def test_meny_offline_cli_reports_loader_and_keeps_source_private(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts.probe_public_sources import main
        raw = sample("<h1>Ukens tilbud</h1><p>Laster produkter</p>")
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "meny.html"
            fixture.write_bytes(raw)
            dest = Path(tmp) / "output"
            buf = StringIO()
            with patch("scripts.probe_public_sources.fetch_spaced",
                       side_effect=AssertionError("must remain offline")), \
                 redirect_stdout(buf):
                self.assertEqual(main(["--source", "meny_weekly",
                                       "--input-html", str(fixture),
                                       "--out", str(dest)]), 0)
            reports = list(dest.rglob("*.json"))
            self.assertEqual(len(reports), 1)
            data = json.loads(reports[0].read_text())
            self.assertEqual(data["evidence_state"], "structure_only_needs_review")
            self.assertEqual(data["price_verification"], "none")
            self.assertIn("MENY readiness: article H3=0", buf.getvalue())
            self.assertIn("store-specific price verified=False", buf.getvalue())

    def test_missing_visible_text_or_json_only_does_not_claim_parsed(self):
        raw = sample("<h1>Velkommen</h1>")
        report = analyze_html("rema_locked", raw)
        self.assertEqual(report["marker_phrases_found"], [])
        self.assertEqual(report["evidence_state"], "insufficient_visible_text")

    def test_bad_nonhtml_oversized_and_utf8_fail_without_side_effects(self):
        for raw in (b"", b'{"success":true}', b"\xff\xfe",
                    b"<!doctype html>" + b"A" * MAX_HTML_BYTES):
            with self.subTest(length=len(raw)):
                with self.assertRaises(SourceProbeError):
                    analyze_html("rema_locked", raw)

    def test_immutable_private_snapshot_and_no_product_database(self):
        raw = sample("<h1>Låst pris</h1><h2>Kampanjeprodukter</h2>"
                     "<h3>EGG 6PK</h3><p>26,90 kr</p>")
        report = analyze_html("rema_locked", raw)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "private"
            path, info = save_private_snapshot(raw, report, root)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(json.loads(info.read_text())["sha256"], report["sha256"])
            other = dict(report, fetched_at="2026-10-10T19:00:00Z")
            same, same_info = save_private_snapshot(raw, other, root)
            self.assertEqual((same, same_info), (path, info))
            self.assertNotEqual(json.loads(info.read_text())["fetched_at"],
                                other["fetched_at"])
            self.assertEqual(len(list(root.rglob("*.html"))), 1)
            self.assertEqual(len(list(root.rglob("*.json"))), 1)
            path.write_text("tampered")
            with self.assertRaisesRegex(SourceProbeError, "inconsistent"):
                save_private_snapshot(raw, report, root)

    def test_one_public_get_only_without_auth_headers(self):
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "text/html; charset=utf-8"}
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def geturl(self): return SOURCES["kiwi_locked"].url
            def read(self, _: int): return sample("<h1>Prislås</h1>")
        with patch("nutrivalue.providers.source_probe.urlopen",
                   return_value=FakeResponse()) as call:
            body, url = fetch_once("kiwi_locked")
        self.assertIn(b"Prisl", body)
        self.assertEqual(url, SOURCES["kiwi_locked"].url)
        self.assertEqual(call.call_count, 1)
        req = call.call_args.args[0]
        self.assertEqual(req.full_url, SOURCES["kiwi_locked"].url)
        self.assertNotIn("authorization", req.headers)
        self.assertNotIn("cookie", req.headers)

    def test_unsafe_redirect_or_mime_rejected_even_on_http_200(self):
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __init__(self, url): self.url = url
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def geturl(self): return self.url
            def read(self, _: int): return b"{}"
        with patch("nutrivalue.providers.source_probe.urlopen",
                   return_value=FakeResponse("https://other.example/")):
            with self.assertRaisesRegex(SourceProbeError, "redirect"):
                fetch_once("rema_locked")
        with patch("nutrivalue.providers.source_probe.urlopen",
                   return_value=FakeResponse(SOURCES["rema_locked"].url)):
            with self.assertRaisesRegex(SourceProbeError, "text/html"):
                fetch_once("rema_locked")


    def test_same_host_is_deferred_across_separate_pinned_pages(self):
        from nutrivalue.providers.source_probe import fetch_spaced
        source = SOURCES["sesum_weekly"]
        with tempfile.TemporaryDirectory() as temp, \
             patch("nutrivalue.providers.source_probe.fetch_once",
                   return_value=(sample("<h1>Ukens avis</h1>"), source.url)) as call:
            root = Path(temp)
            fetch_spaced("sesum_weekly", root, now_epoch=1_000_000)
            with self.assertRaisesRegex(SourceProbeError, "deferred until"):
                fetch_spaced("sesum_product", root, now_epoch=1_000_001)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(json.loads(
                (root / ".host-pacing/www.sesum.no.json").read_text())[
                    "not_before_epoch"], 1_000_300)
            fetch_spaced("sesum_product", root, now_epoch=1_000_301)
            self.assertEqual(call.call_count, 2)

    def test_http_429_blocks_other_sesum_page_for_at_least_24_hours(self):
        from io import BytesIO
        from urllib.error import HTTPError
        from nutrivalue.providers.source_probe import fetch_spaced
        failure = HTTPError(SOURCES["sesum_weekly"].url, 429, "Too Many Requests",
                            {"Retry-After": "20"}, BytesIO(b""))
        with tempfile.TemporaryDirectory() as temp, \
             patch("nutrivalue.providers.source_probe.fetch_once",
                   side_effect=failure) as call:
            root = Path(temp)
            with self.assertRaisesRegex(SourceProbeError, "HTTP 429; further requests"):
                fetch_spaced("sesum_weekly", root, now_epoch=1_000_000)
            with self.assertRaisesRegex(SourceProbeError, "deferred until"):
                fetch_spaced("sesum_product", root, now_epoch=1_000_500)
            self.assertEqual(call.call_count, 1)
            state = json.loads((root / ".host-pacing/www.sesum.no.json").read_text())
            self.assertEqual(state["last_status"], "http_429")
            self.assertEqual(state["not_before_epoch"], 1_086_400)

    def test_long_retry_after_is_never_shortened_and_other_host_unaffected(self):
        from io import BytesIO
        from urllib.error import HTTPError
        from nutrivalue.providers.source_probe import fetch_spaced
        failure = HTTPError(SOURCES["sesum_product"].url, 429, "Too Many Requests",
                            {"Retry-After": "172800"}, BytesIO(b""))
        with tempfile.TemporaryDirectory() as temp, \
             patch("nutrivalue.providers.source_probe.fetch_once",
                   side_effect=[failure, (b"html", SOURCES["rema_locked"].url)]) as call:
            root = Path(temp)
            with self.assertRaises(SourceProbeError):
                fetch_spaced("sesum_product", root, now_epoch=1_000_000)
            self.assertEqual(json.loads(
                (root / ".host-pacing/www.sesum.no.json").read_text())[
                    "not_before_epoch"], 1_172_800)
            raw, _ = fetch_spaced("rema_locked", root, now_epoch=1_000_010)
            self.assertEqual(raw, b"html")
            self.assertEqual(call.call_count, 2)

    def test_invalid_persistent_host_state_fails_without_network(self):
        from nutrivalue.providers.source_probe import fetch_spaced
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subdir = root / ".host-pacing"
            subdir.mkdir()
            (subdir / "www.sesum.no.json").write_text("oops", encoding="utf-8")
            with patch("nutrivalue.providers.source_probe.fetch_once") as fetch:
                with self.assertRaisesRegex(SourceProbeError, "state is invalid"):
                    fetch_spaced("sesum_weekly", root, now_epoch=1_000_000)
                fetch.assert_not_called()

    def test_offline_cli_replay_creates_report_without_network(self):
        from scripts.probe_public_sources import main
        raw = sample("<h1>Låst pris</h1><h2>Kampanjeprodukter</h2>"
                     "<h3>EGG 6PK</h3><p>26,90 kr</p>")
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "source.html"
            fixture.write_bytes(raw)
            root = Path(temp) / "output"
            with patch("scripts.probe_public_sources.fetch_spaced",
                       side_effect=AssertionError("must stay offline")):
                self.assertEqual(main(["--source", "rema_locked",
                                       "--input-html", str(fixture),
                                       "--out", str(root)]), 0)
            self.assertEqual(len(list(root.rglob("*.json"))), 1)
            self.assertEqual(len(list(root.rglob("*.html"))), 1)

    def test_coop_offline_cli_reports_explicit_article_readiness(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts.probe_public_sources import main
        raw = sample(
            "<h2>Ukens tilbud</h2><h3>Les ukens kundeavis</h3>"
            "<article><h3>▆▆▆▆▆▆▆▆</h3><span>1 for -1%</span></article>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "coop.html"
            fixture.write_bytes(raw)
            dest = Path(tmp) / "diagnostic-only"
            out = StringIO()
            with patch("scripts.probe_public_sources.fetch_spaced",
                       side_effect=AssertionError("offline replay only")), \
                 redirect_stdout(out):
                self.assertEqual(main([
                    "--source", "coop_prix_weekly",
                    "--input-html", str(fixture),
                    "--out", str(dest),
                ]), 0)
            report_files = list(dest.rglob("*.json"))
            self.assertEqual(len(report_files), 1)
            report = json.loads(report_files[0].read_text())
            self.assertEqual(report["evidence_state"], "structure_only_needs_review")
            self.assertEqual(report["coop_article_count"], 1)
            self.assertEqual(report["coop_placeholder_article_h3_count"], 1)
            self.assertTrue(report["coop_requires_render_review"])
            self.assertIn("articles=1", out.getvalue())
            self.assertIn("render review required=True", out.getvalue())
            self.assertEqual(report["price_verification"], "none")


if __name__ == "__main__":
    unittest.main()
