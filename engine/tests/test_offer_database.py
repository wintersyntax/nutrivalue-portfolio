"""TASK-052: synthetic weekly offer-database import; NO HTTP, temp files only."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from dataclasses import replace
from unittest import mock

from nutrivalue.offer_categories import TAXONOMY_VERSION, classify_offer
from nutrivalue.offer_database import OfferImportError, discover_runs, import_run, reclassify
from nutrivalue.providers.tjek_offline import CHAINS
from nutrivalue.providers.tjek_weekly_collect import collect_week

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "analytics"
AT = "2026-10-15T12:00:00+02:00"
START, END = "2026-10-12T00:00:00+0200", "2026-10-19T00:00:00+0200"
TEXT = {"faa0Ym": ("Kyllingfilet", "Medlemspris 89,90. Partivare"),
        "257bxm": ("Skyr naturell", "3 for 50"),
        "5b11sm": ("Kjøttdeig", "Pant kommer i tillegg")}


def catalog(dealer):
    label = "Joker kundeavis 12.10.–17.10." if dealer == "b3e8Fm" else "Uke 42"
    return {"id": f"c-{dealer}", "dealer_id": dealer, "label": label,
            "run_from": START, "run_till": END, "publish": "2026-10-11T18:00:00+0200",
            "offer_count": 3, "all_stores": False, "types": ["paged"], "page_count": 2}


def offer(dealer, oid):
    heading, description = TEXT.get(dealer, ("SYNTHETIC FOOD", "199,75 pr. kg"))
    price = 50.0 if dealer == "257bxm" else 89.9
    return {"id": oid, "dealer_id": dealer, "catalog_id": f"c-{dealer}",
            "heading": heading, "description": description,
            "pricing": {"price": price, "currency": "NOK"},
            "quantity": {"unit": {"symbol": "g"}, "size": {"from": 400, "to": 400},
                         "pieces": {"from": 1, "to": 1}},
            "run_from": START, "run_till": END}


class Fake:
    def __call__(self, endpoint, params):
        if endpoint == "catalogs":
            return [catalog(params["dealer_ids"])]
        if endpoint == "offers":
            dealer = params["catalog_id"][2:]
            return [offer(dealer, f"{dealer}-o-1")][params["offset"]:]
        if endpoint.endswith("/hotspots"):
            dealer = endpoint.split("/")[1][2:]
            return [{"type": "offer", "offer": {"id": f"{dealer}-o-{n}"},
                     "locations": {str(n): [[0.1, 0.1], [0.5, 0.5]]}} for n in (1, 2)]
        if endpoint.startswith("offers/"):
            oid = endpoint.split("/")[1]
            return offer(oid.split("-o-")[0], oid)
        raise AssertionError(endpoint)


class BadKiwi(Fake):
    """TASK-079: KIWI offers break validation - one offer (``one``) or all (``all``)."""

    def __init__(self, mode):
        self.mode = mode

    def __call__(self, endpoint, params):
        result = super().__call__(endpoint, params)
        if endpoint.startswith("offers/") and endpoint.endswith("257bxm-o-2"):
            result["pricing"]["pre_price"] = 41.905           # a real third decimal
        if self.mode == "all" and endpoint == "offers" and params["catalog_id"] == "c-257bxm":
            for item in result:
                item["pricing"]["currency"] = "EUR"
        return result


class OfferDatabaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.weekly = self.tmp / "weekly"
        self.db = self.tmp / "offers.sqlite3"

    def run_collector(self, run_id, *, hotspots=True, minute=1, allow_repeat=False, fake=None):
        collect_week(as_of=AT, out_dir=self.weekly, get_json=fake or Fake(), run_id=run_id,
                     captured_at=datetime.fromisoformat(f"2026-10-15T12:{minute:02d}:00+02:00"),
                     hotspots=hotspots, allow_repeat=allow_repeat)
        return self.weekly / "2026-W42" / f"run-{run_id}"

    def query(self, sql, *args):
        with sqlite3.connect(self.db) as db:
            return db.execute(sql, args).fetchall()

    def test_hotspot_run_imports_acquisition_conditions_categories_and_coverage(self):
        run = self.run_collector("20261015T120100Z")
        result = import_run(run, self.db, SCHEMAS)
        self.assertEqual((result["imported"], result["offers"], result["chains"]), (True, 18, 9))
        self.assertEqual(self.query(
            "SELECT acquisition, COUNT(*) FROM v_week_offers GROUP BY 1 ORDER BY 1"),
            [("hotspot_detail", 9), ("listing", 9)])
        self.assertEqual(self.query(
            "SELECT hotspot_pages FROM v_week_offers WHERE offer_id='faa0Ym-o-2'"), [("[2]",)])
        rema = self.query("""SELECT explicit_member_ore, limited_stock, member_condition
                             FROM v_week_offers WHERE offer_id='faa0Ym-o-1'""")
        self.assertEqual(rema, [(8990, 1, 1)])
        kiwi = self.query("""SELECT price_kind, bundle_units, bundle_total_ore
                             FROM v_week_offers WHERE offer_id='257bxm-o-1'""")
        self.assertEqual(kiwi, [("advertised_bundle_total", 3, 5000)])
        self.assertEqual(self.query(
            "SELECT pant_mentioned FROM v_week_offers WHERE offer_id='5b11sm-o-1'"), [(1,)])
        self.assertEqual(self.query(
            "SELECT food_category FROM v_week_offers WHERE offer_id='faa0Ym-o-1'"), [("meat",)])
        coverage = self.query("""SELECT declared_offers, listing_offers, hotspot_offers,
                                        detail_recovered, offers_saved, coverage_pct
                                 FROM v_week_coverage WHERE chain_name='REMA 1000'""")
        self.assertEqual(coverage, [(3, 1, 2, 1, 2, 66.7)])
        self.assertTrue(all(r[0] == "source_only" for r in self.query(
            "SELECT DISTINCT review_state FROM v_week_offers")))

    def test_reclassify_adds_current_taxonomy_for_earlier_imports(self):
        # TASK-073: offers imported under an older taxonomy get the current one
        # appended; the older rows stay and the view reads the newest version.
        def older(heading, description=None, grams=None):
            return replace(classify_offer(heading, description, grams),
                           taxonomy_version=TAXONOMY_VERSION - 1, food_key=None)
        with mock.patch("nutrivalue.offer_database.classify_offer", older):
            import_run(self.run_collector("20261015T120100Z"), self.db, SCHEMAS)
        self.assertEqual(self.query("SELECT food_key FROM v_week_offers "
                                    "WHERE offer_id='faa0Ym-o-1'"), [(None,)])
        self.assertEqual(reclassify(self.db), 18)
        self.assertEqual(reclassify(self.db), 0)
        self.assertEqual(self.query("SELECT food_key FROM v_week_offers "
                                    "WHERE offer_id='faa0Ym-o-1'"), [("chicken_breast",)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM tjek_offer_categories"), [(36,)])
        self.assertEqual(reclassify(self.tmp / "absent.sqlite3"), 0)

    def test_reimport_is_noop_and_changed_manifest_fails_closed(self):
        run = self.run_collector("20261015T120100Z")
        import_run(run, self.db, SCHEMAS)
        again = import_run(run, self.db, SCHEMAS)
        self.assertFalse(again["imported"])
        self.assertEqual(self.query("SELECT COUNT(*) FROM tjek_offers"), [(18,)])
        manifest = run / "manifest.json"
        data = json.loads(manifest.read_text())
        data["batch_status"] = "edited"
        manifest.chmod(0o600)
        manifest.write_text(json.dumps(data))
        with self.assertRaises(OfferImportError):
            import_run(run, self.db, SCHEMAS)
        self.assertEqual(self.query("SELECT batch_status FROM tjek_runs"),
                         [("partial_or_unproven",)])

    def test_tampered_snapshot_writes_nothing(self):
        run = self.run_collector("20261015T120100Z")
        target = run / "faa0Ym.json"
        target.chmod(0o600)
        target.write_bytes(target.read_bytes().replace(b"89.9", b"19.9"))
        with self.assertRaises(OfferImportError):
            import_run(run, self.db, SCHEMAS)
        self.assertFalse(self.db.exists() and self.query("SELECT * FROM tjek_runs"))

    def test_missing_hotspot_file_fails_closed(self):
        run = self.run_collector("20261015T120100Z")
        (run / "faa0Ym.hotspots.json").unlink()
        with self.assertRaises(OfferImportError):
            import_run(run, self.db, SCHEMAS)

    def test_legacy_listing_only_run_and_latest_run_selection(self):
        legacy = self.run_collector("20261015T120100Z", hotspots=False)
        newer = self.run_collector("20261015T120500Z", minute=5, allow_repeat=True)
        for run in discover_runs(self.weekly):
            import_run(run, self.db, SCHEMAS)
        self.assertEqual(self.query(
            "SELECT DISTINCT acquisition, hotspot_pages FROM tjek_offer_acquisition a "
            "JOIN tjek_run_chains rc ON rc.snapshot_sha256=a.snapshot_sha256 "
            "WHERE rc.run_id=?", f"2026-W42/{legacy.name}"), [("listing", None)])
        self.assertEqual(self.query("SELECT run_id FROM v_week_run"),
                         [(f"2026-W42/{newer.name}",)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM v_week_offers"), [(18,)])

    def test_halted_run_is_recorded_but_never_selected(self):
        halted = self.weekly / "2026-W42" / "run-20261015T110000Z"
        halted.mkdir(parents=True)
        (halted / "manifest.json").write_text(json.dumps({
            "week": "2026-W42", "fetched_at": "2026-10-15T09:00:00+00:00", "as_of": AT,
            "batch_status": "partial_or_unproven", "halted_reason": "Tjek HTTP 429; no retry",
            "http_requests": 1, "total_saved_offers": 0,
            "chains": [{"dealer_id": d, "chain": n, "status": "not_attempted",
                        "catalogs_selected": 0, "offers_saved": 0,
                        "source_count_shortfall": 0, "snapshot_file": None}
                       for d, n in CHAINS.items()]}))
        import_run(halted, self.db, SCHEMAS)
        self.assertEqual(self.query("SELECT COUNT(*) FROM tjek_runs"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM v_week_run"), [(0,)])

    def test_one_invalid_offer_is_dropped_and_the_store_kept(self):
        run = self.run_collector("20261015T120100Z", fake=BadKiwi("one"))
        manifest = json.loads((run / "manifest.json").read_text())
        kiwi = next(c for c in manifest["chains"] if c["dealer_id"] == "257bxm")
        self.assertEqual((kiwi["status"], kiwi["offers_saved"], kiwi["offers_rejected"]),
                         ("offers_rejected", 1, 1))
        self.assertIn("max two decimals", kiwi["rejected_reasons"][0])
        self.assertTrue((run / "257bxm.rejected.json").is_file())
        self.assertFalse((run / "257bxm.unvalidated").exists())
        import_run(run, self.db, SCHEMAS)                 # hotspot file stays consistent
        self.assertEqual(self.query(
            "SELECT offer_id FROM v_week_offers WHERE dealer_id='257bxm'"), [("257bxm-o-1",)])

    def _health(self):
        with sqlite3.connect(self.db) as db:
            db.executescript((SCHEMAS / "promotion_extraction.sql").read_text())
            return {row[0]: row[1:] for row in db.execute(
                "SELECT chain_name, offers_light, promotions_light, light "
                "FROM v_week_chain_health")}

    def test_later_run_without_a_store_keeps_its_earlier_offers_and_turns_amber(self):
        # TASK-079: W44 Thursday lost KIWI and became the selected run for the week.
        import_run(self.run_collector("20261015T120100Z"), self.db, SCHEMAS)
        later = self.run_collector("20261015T120500Z", minute=5, allow_repeat=True,
                                   fake=BadKiwi("all"))
        import_run(later, self.db, SCHEMAS)
        self.assertEqual(self.query("SELECT COUNT(*) FROM v_week_offers"), [(18,)])
        self.assertEqual(self.query(
            "SELECT DISTINCT run_id FROM v_week_offers WHERE dealer_id='257bxm'"),
            [("2026-W42/run-20261015T120100Z",)])
        health = self._health()
        self.assertEqual(health["KIWI"][0], "amber")
        self.assertEqual(health["REMA 1000"][0], "green")
        self.assertTrue(all(v[1] == "red" for v in health.values()))   # no pages read yet

    def test_store_missing_in_every_run_of_the_week_is_red(self):
        import_run(self.run_collector("20261015T120100Z", fake=BadKiwi("all")), self.db, SCHEMAS)
        health = self._health()
        self.assertEqual((health["KIWI"][0], health["KIWI"][2]), ("red", "red"))
        self.assertEqual(health["MENY"][0], "green")
        self.assertEqual(self.query(
            "SELECT COUNT(*) FROM v_week_offers WHERE dealer_id='257bxm'"), [(0,)])

    def test_tables_are_append_only(self):
        import_run(self.run_collector("20261015T120100Z"), self.db, SCHEMAS)
        for sql in ("UPDATE tjek_runs SET batch_status='x'",
                    "DELETE FROM tjek_run_chains",
                    "UPDATE tjek_offer_acquisition SET acquisition='listing'",
                    "DELETE FROM tjek_offer_conditions",
                    "DELETE FROM tjek_offer_categories"):
            with self.assertRaises(sqlite3.DatabaseError):
                with sqlite3.connect(self.db) as db:
                    db.execute(sql)

    def test_rejects_non_run_directories(self):
        with self.assertRaises(OfferImportError):
            import_run(self.tmp, self.db, SCHEMAS)

    def test_cli_refuses_database_outside_private_dir(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts/import_tjek_week.py"), "--all",
             "--weekly-root", str(self.weekly), "--db", str(self.db)],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("data/private", out.stderr)


if __name__ == "__main__":
    unittest.main()
