"""TASK-051: synthetic hotspot recovery and page-image capture; NO actual HTTP."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nutrivalue.providers.tjek_offline import CHAINS, parse_snapshot
from nutrivalue.providers.tjek_weekly_collect import (
    CollectionError, _aware, _hotspot_offers, collect_week, live_bytes, live_json,
)

AT = "2026-10-15T12:00:00+02:00"
START = "2026-10-12T00:00:00+0200"   # provider style offset without colon
END = "2026-10-19T00:00:00+0200"
RUN_ID = "20261015T120000Z"
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic-page"
IMG = "https://image-transformer-api.tjek.com/?u=s3%3A%2F%2Fx%2F{cid}%2Fp-{n}.webp&w=1400"


def catalog(dealer, pages=2, count=3):
    label = "Joker kundeavis 12.10.–17.10." if dealer == "b3e8Fm" else "Uke 42"
    return {"id": f"c-{dealer}", "dealer_id": dealer, "label": label,
            "run_from": START, "run_till": END, "publish": "2026-10-11T18:00:00+0200",
            "offer_count": count, "all_stores": False, "types": ["paged"],
            "page_count": pages}


def offer(dealer, oid):
    return {"id": oid, "dealer_id": dealer, "catalog_id": f"c-{dealer}",
            "heading": "SYNTHETIC FOOD", "description": "199,75 pr. kg",
            "pricing": {"price": 79.9, "currency": "NOK"},
            "quantity": {"unit": {"symbol": "g"}, "size": {"from": 400, "to": 400},
                         "pieces": {"from": 1, "to": 1}},
            "run_from": START, "run_till": END}


def spot(oid, page="1"):
    return {"type": "offer", "id": f"h-{oid}", "offer": {"id": oid},
            "locations": {page: [[0.1, 0.1], [0.4, 0.4]]}}


class Fake:
    """Listing omits o-2; hotspots include o-1 and o-2 on pages 1 and 2."""

    def __init__(self):
        self.calls = []
        self.detail_override = {}
        self.pages_override = None

    def __call__(self, endpoint, params):
        self.calls.append(endpoint)
        if endpoint == "catalogs":
            return [catalog(params["dealer_ids"])]
        if endpoint == "offers":
            dealer = params["catalog_id"][2:]
            return [offer(dealer, f"{dealer}-o-1")][params["offset"]:]
        if endpoint.endswith("/hotspots"):
            dealer = endpoint.split("/")[1][2:]
            return [spot(f"{dealer}-o-1"), spot(f"{dealer}-o-2", "2"),
                    {"type": "link", "id": "ignored"}]
        if endpoint.endswith("/pages"):
            cid = endpoint.split("/")[1]
            if self.pages_override is not None:
                return self.pages_override
            return [{"zoom": IMG.format(cid=cid, n=n)} for n in (1, 2)]
        if endpoint.startswith("offers/"):
            oid = endpoint.split("/")[1]
            if oid in self.detail_override:
                return self.detail_override[oid]
            return offer(oid.split("-o-")[0], oid)
        raise AssertionError(endpoint)


class HotspotCollectorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "private"
        self.fake = Fake()
        self.images = []

    def get_bytes(self, url):
        self.images.append(url)
        return JPEG

    def collect(self, **kw):
        return collect_week(
            as_of=AT, out_dir=self.out, get_json=self.fake, run_id=RUN_ID,
            captured_at=datetime.fromisoformat("2026-10-15T12:01:00+02:00"), **kw)

    def run_dir(self):
        return self.out / "2026-W42" / f"run-{RUN_ID}"

    def test_hotspot_only_offers_are_recovered_by_id_into_valid_snapshot(self):
        report = self.collect(hotspots=True)
        self.assertEqual(report["offer_detail_requests"], 9)
        self.assertEqual(report["http_requests"], 27)
        for row in report["chains"]:
            self.assertEqual((row["listing_offers"], row["hotspot_offers"],
                              row["detail_recovered"]), (1, 2, 1))
            self.assertEqual(row["offers_saved"], 2)
            self.assertEqual(row["source_count_shortfall"], 1)
        for dealer in CHAINS:
            snap = parse_snapshot((self.run_dir() / f"{dealer}.json").read_bytes(),
                                  f"{dealer}.json")
            self.assertEqual(len(snap.offers), 2)
            spots = json.loads((self.run_dir() / f"{dealer}.hotspots.json").read_text())
            cat = spots["catalogs"][0]
            self.assertEqual(cat["listing_offer_ids"], [f"{dealer}-o-1"])
            self.assertEqual(cat["detail_recovered_ids"], [f"{dealer}-o-2"])
            self.assertEqual(cat["hotspot_offer_pages"][f"{dealer}-o-2"], [2])
            self.assertEqual(os.stat(self.run_dir() / f"{dealer}.hotspots.json").st_mode & 0o777, 0o600)
        manifest = (self.run_dir() / "manifest.json").read_text()
        self.assertNotIn("SYNTHETIC FOOD", manifest)

    def test_default_off_keeps_legacy_request_shape(self):
        report = self.collect()
        self.assertFalse(report["hotspots_enabled"])
        self.assertNotIn("listing_offers", report["chains"][0])
        self.assertFalse(any("/" in call for call in self.fake.calls))

    def test_foreign_catalog_detail_fails_closed(self):
        bad = offer("faa0Ym", "faa0Ym-o-2")
        bad["catalog_id"] = "c-other"
        self.fake.detail_override["faa0Ym-o-2"] = bad
        report = self.collect(hotspots=True)
        self.assertIn("another dealer/catalog", report["halted_reason"])
        self.assertEqual(report["batch_status"], "partial_or_unproven")

    def test_detail_budget_is_enforced(self):
        report = self.collect(hotspots=True, max_detail_requests=3)
        self.assertEqual(report["halted_reason"], "offer detail request budget exhausted")
        self.assertEqual(report["offer_detail_requests"], 3)

    def test_page_images_saved_with_hash_manifest(self):
        report = self.collect(page_images=True, get_bytes=self.get_bytes)
        self.assertEqual(report["page_image_requests"], 18)
        dealer = "faa0Ym"
        pages = json.loads((self.run_dir() / f"{dealer}.pages.json").read_text())
        self.assertTrue(pages["complete"])
        self.assertEqual([p["page"] for p in pages["pages"]], [1, 2])
        first = pages["pages"][0]
        raw = (self.run_dir() / first["file"]).read_bytes()
        self.assertEqual(first["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(first["file"].endswith(".jpg"))
        self.assertEqual(os.stat(self.run_dir() / first["file"]).st_mode & 0o777, 0o600)
        self.assertEqual(report["chains"][0]["page_images_saved"], 2)

    def test_bad_image_is_recorded_without_halting_other_chains(self):
        report = self.collect(page_images=True, get_bytes=lambda url: b"<html>")
        self.assertIsNone(report["halted_reason"])
        self.assertTrue(all(row["page_image_error"] for row in report["chains"]))
        self.assertTrue(all(row["offers_saved"] == 1 for row in report["chains"]))

    def test_page_list_must_match_page_count(self):
        self.fake.pages_override = [{"zoom": IMG.format(cid="c", n=1)}]
        report = self.collect(page_images=True, get_bytes=self.get_bytes)
        self.assertIn("page_count", report["halted_reason"])

    def test_page_image_host_is_restricted(self):
        self.fake.pages_override = [{"zoom": "https://evil.example/p1.jpg"},
                                    {"zoom": "https://evil.example/p2.jpg"}]
        report = self.collect(page_images=True, get_bytes=self.get_bytes)
        self.assertIn("image host", report["halted_reason"])
        self.assertEqual(self.images, [])

    def test_page_images_require_getter_and_bounds(self):
        with self.assertRaises(CollectionError):
            self.collect(page_images=True)
        with self.assertRaises(CollectionError):
            self.collect(hotspots=True, max_detail_requests=401)


class HelperTests(unittest.TestCase):
    def test_provider_offsets_without_colon_parse(self):
        self.assertEqual(_aware("2026-10-18T22:00:00+0000").utcoffset().total_seconds(), 0)

    def test_hotspot_page_numbers_are_validated(self):
        with self.assertRaises(CollectionError):
            _hotspot_offers([spot("abc-1", "9")], page_count=2)
        with self.assertRaises(CollectionError):
            _hotspot_offers([{"type": "offer", "offer": {"id": "../x"}, "locations": {}}],
                            page_count=2)
        self.assertEqual(_hotspot_offers([spot("abc-1", "2"), spot("abc-1", "1")],
                                         page_count=2), {"abc-1": [1, 2]})

    def test_live_json_endpoint_allowlist(self):
        for bad in ("catalogs/../x/hotspots", "offers/a/b", "dealers", "catalogs/x/pdf"):
            with self.assertRaises(CollectionError):
                live_json(bad, {}, opener=lambda *a, **k: self.fail("no HTTP"))

    def test_live_bytes_rejects_foreign_host_before_http(self):
        with self.assertRaises(CollectionError):
            live_bytes("https://example.com/p.jpg", opener=lambda *a, **k: self.fail("no HTTP"))
        with self.assertRaises(CollectionError):
            live_bytes("http://image-transformer-api.tjek.com/p.jpg",
                       opener=lambda *a, **k: self.fail("no HTTP"))


if __name__ == "__main__":
    unittest.main()
