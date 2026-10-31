"""TASK-032: private public-Extra-store geo checks and nine-chain integration."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nutrivalue.providers.tjek_extra_store import (
    check_public_extra_store, load_public_extra_store, load_store_listing,
)
from nutrivalue.providers.tjek_weekly_collect import (
    CollectionError, collect_week,
)
from nutrivalue.providers.tjek_offline import CHAINS, parse_snapshot

AT = "2026-10-15T12:00:00+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-19T00:00:00+02:00"
STAMP = datetime.fromisoformat(AT)
LOCAL = "W8l7eqAB"
GLOBAL = "WprgprHV"


def store(name="Extra Pilestredet Park", id="tjek-store-2458", lat=59.9222, long=10.742):
    return {
        "name": name, "id": id, "dealer_id": "80742m",
        "latitude": lat, "longitude": long,
        "city": "Oslo", "street": "Synthetic Street",
    }


def catalog(dealer, id=None, *, count=1, name="Uke 42", all_stores=False):
    return {
        "id": id or f"cat-{dealer}", "dealer_id": dealer,
        "label": name, "all_stores": all_stores,
        "offer_count": count, "run_from": START, "run_till": END,
        "publish": START, "types": ["paged"],
    }


def offer(dealer, cid):
    return {
        "id": f"test-offer-{dealer}", "catalog_id": cid, "dealer_id": dealer,
        "heading": "SYNTHETIC FOOD", "description": "Test 500 g",
        "pricing": {"price": 29.90, "currency": "NOK"},
        "quantity": {"unit": {"symbol": "g"}, "size": {"from": 500, "to": 500},
                     "pieces": {"from": 1, "to": 1}},
        "run_from": START, "run_till": END,
    }


class Fake:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.fail:
            raise CollectionError("Tjek HTTP 429; no retry")
        if endpoint == "catalogs":
            dealer = params["dealer_ids"]
            if dealer == "80742m":
                self.geopoint = (params.get("r_lat"), params.get("r_lng"))
                return [
                    catalog(dealer, id=LOCAL, count=1, name=""),
                    catalog(dealer, id=GLOBAL, count=0, name="Global magazine",
                            all_stores=True),
                ]
            return [catalog(
                dealer, name=("Joker kundeavis 12.10.–17.10."
                              if dealer == "b3e8Fm" else "Uke 42"))]
        if endpoint == "offers":
            cid = params["catalog_id"]
            dealer = "80742m" if cid == LOCAL else cid.removeprefix("cat-")
            rows = [offer(dealer, cid)]
            return rows[params["offset"]:params["offset"] + params["limit"]]
        raise AssertionError(endpoint)


class ExtraShopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "private"
        self.root.mkdir()
        self.snapshots = self.root / "2026-W42" / "extra-api-investigation"
        self.snapshots.mkdir(parents=True)
        self.shops_file = self.snapshots / "oslo_stores.json"
        self.shops_file.write_text(json.dumps([store(),
            store(name="Extra Bjølsen", id="tjek-shop-bjolsen",
                  lat=59.933, long=10.759)]))
        self.city = self.snapshots / "oslo_catalogs.json"
        self.city.write_text(json.dumps([
            catalog("80742m", LOCAL, count=118, name=""),
            catalog("80742m", GLOBAL, count=0, name="Global", all_stores=True),
        ]))
        self.api = Fake()

    def test_saved_public_shop_name_resolution_is_unique_and_local(self):
        shop = load_public_extra_store(
            stores_path=self.shops_file, private_root=self.root,
            store_name="Pilestredet Park")
        self.assertEqual(shop["name"], "Extra Pilestredet Park")
        self.assertEqual(shop["store_id"], "tjek-store-2458")
        self.assertEqual(len(load_store_listing(
            stores_path=self.shops_file, private_root=self.root)), 2)
        self.assertFalse(shop["verified_shelf_price"])

    def test_unknown_shop_fails_closed(self):
        with self.assertRaisesRegex(CollectionError, "found 0"):
            load_public_extra_store(stores_path=self.shops_file,
                                    private_root=self.root, store_name="Does Not Exist")

    def test_ambiguous_shop_name_rejected(self):
        self.shops_file.write_text(json.dumps([store(id="one"), store(id="two")]))
        with self.assertRaisesRegex(CollectionError, "found 2"):
            load_public_extra_store(stores_path=self.shops_file,
                                    private_root=self.root,
                                    store_name="Pilestredet Park")

    def test_foreign_dealer_private_path_and_invalid_coords_rejected(self):
        for item in ([store(id="a", lat=0)], [store(id="b", long=-10)],
                     [dict(store(), dealer_id="other")]):
            self.shops_file.write_text(json.dumps(item))
            with self.assertRaises(CollectionError):
                load_store_listing(stores_path=self.shops_file,
                                   private_root=self.root)
        with self.assertRaises(CollectionError):
            load_store_listing(stores_path=self.root.parent / "other.json",
                               private_root=self.root)

    def test_shop_geo_probe_exactly_one_request_and_original_city_comparison(self):
        shop = load_public_extra_store(
            stores_path=self.shops_file, private_root=self.root,
            store_name="Extra Pilestredet Park")
        report = check_public_extra_store(
            private_root=self.root, store=shop, as_of=AT,
            get_json=self.api, city_reference_path=self.city,
            captured_at=STAMP, run_id="20261015T100500Z")
        self.assertEqual(report["http_requests"], 1)
        self.assertEqual(self.api.geopoint, (59.9222, 10.742))
        self.assertEqual(report["resolution"]["catalog_id"], LOCAL)
        self.assertEqual(report["resolution"]["city_reference_comparison"], "same")
        self.assertFalse(report["resolution"]["verified_actual_shelf_price"])
        self.assertEqual(len(self.api.calls), 1)
        folder = self.root / "2026-W42" / "extra-store-check-20261015T100500Z"
        self.assertTrue((folder / "store.catalogs.json").exists())
        self.assertEqual((folder / "store.catalogs.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(CollectionError, "already attempted"):
            check_public_extra_store(private_root=self.root, store=shop,
                                     as_of=AT, get_json=self.api)
        self.assertEqual(len(self.api.calls), 1)

    def test_shop_probe_http_429_stops_and_saves_partial_manifest(self):
        shop = load_public_extra_store(
            stores_path=self.shops_file, private_root=self.root,
            store_name="Pilestredet Park")
        self.api.fail = True
        result = check_public_extra_store(
            private_root=self.root, store=shop, as_of=AT,
            get_json=self.api, run_id="20261015T100500Z")
        self.assertEqual(result["http_requests"], 1)
        self.assertIsNone(result["resolution"])
        self.assertIn("429", result["halted_reason"])

    def test_nine_chain_geo_store_selects_only_one_blank_label_extra_catalog(self):
        store_info = load_public_extra_store(
            stores_path=self.shops_file, private_root=self.root,
            store_name="Pilestredet Park")
        output = self.root / "new-weekly-archive"
        report = collect_week(as_of=AT, out_dir=output, get_json=self.api,
                              extra_store=store_info,
                              captured_at=STAMP, run_id="20261015T100500Z")
        self.assertEqual(report["http_requests"], 18)
        self.assertEqual(report["candidate_chains"], 9)
        self.assertTrue(report["extra_store_geographic_selection"])
        extra = next(r for r in report["chains"] if r["dealer_id"] == "80742m")
        self.assertEqual(extra["extra_public_store_geo"]["catalog_id"], LOCAL)
        self.assertEqual(extra["catalogs_selected"], 1)
        self.assertEqual(extra["offers_saved"], 1)
        data = output / "2026-W42" / "run-20261015T100500Z" / "80742m.json"
        parsed = parse_snapshot(data.read_bytes(), "80742m.json")
        self.assertEqual(parsed.catalogs[0][1], LOCAL)
        self.assertEqual(parsed.offers[0][-1], "source_only")
        self.assertEqual(self.api.geopoint, (59.9222, 10.742))

    def test_empty_or_ambiguous_geographic_selection_fails_no_second_catalog(self):
        info = load_public_extra_store(
            stores_path=self.shops_file, private_root=self.root,
            store_name="Pilestredet Park")
        class Ambiguous(Fake):
            def __call__(self, endpoint, params):
                if endpoint == "catalogs" and params["dealer_ids"] == "80742m":
                    return [catalog("80742m", LOCAL, name=""),
                            catalog("80742m", "second-regional", name="")]
                return super().__call__(endpoint, params)
        api = Ambiguous()
        report = collect_week(as_of=AT, out_dir=self.root / "collection",
                              get_json=api, extra_store=info,
                              captured_at=STAMP, run_id="20261015T100500Z")
        self.assertIn("exactly one", report["halted_reason"])
        extra = next(r for r in report["chains"] if r["dealer_id"] == "80742m")
        self.assertEqual(extra["status"], "error")
        self.assertEqual(extra["catalogs_selected"], 0)
        self.assertTrue(all(call[1].get("catalog_id") != "second-regional"
                            for call in api.calls))

    def test_invalid_manual_extra_coordinates_rejected_before_http(self):
        store_info = load_public_extra_store(
            stores_path=self.shops_file, private_root=self.root,
            store_name="Pilestredet Park")
        for bad in (
            dict(store_info, latitude=1.23),
            dict(store_info, evidence="untrusted arbitrary home coordinates"),
        ):
            with self.assertRaises(CollectionError):
                collect_week(as_of=AT, out_dir=self.root / "weekly",
                             get_json=self.api, extra_store=bad)
        self.assertEqual(self.api.calls, [])


if __name__ == "__main__":
    unittest.main()
