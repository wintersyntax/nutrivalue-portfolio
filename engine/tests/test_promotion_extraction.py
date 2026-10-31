"""TASK-055: weekly promotion extraction into the offer database. NO network."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nutrivalue.promotion_benchmark import result_path, run
from nutrivalue.promotion_extraction import (
    METHOD, PAIR, ExtractionError, OcrLookup, build, describe, extraction_id, import_extraction,
    page_image, record_review, report, review_items,
)

ROOT = Path(__file__).resolve().parents[1]
A, B = PAIR
CATALOGUE = {m: {"prompt_usd_per_token": 1e-7, "completion_usd_per_token": 5e-7,
                 "supported_parameters": ["structured_outputs"],
                 "input_modalities": ["text", "image"]} for m in PAIR}


def item(kind="percent_off", percent=None, n=None, m=None, amount=None, member=False,
         scope="Synthetic group"):
    return {"kind": kind, "n": n, "m": m, "percent": percent, "amount_nok": amount,
            "member_only": member, "scope": scope}


def answer(page_class, *items):
    return json.dumps({"page_class": page_class, "promotions": list(items)})


class FakeClient:
    def __init__(self, answers):
        self.answers = answers

    def complete(self, model, prompt, image, schema, *, structured, temperature, max_tokens,
                 provider=None):
        return {"content": self.answers[(model, image[-1])], "cost_usd": 0.0005,
                "prompt_tokens": 4000, "completion_tokens": 100, "latency_s": 1.0,
                "provider": "Anthropic" if model == A else "OpenAI"}


ANSWERS = {
    (A, 1): answer("promotion", item("n_for_m", n=3, m=2, scope="Plukk & miks fisk"),
                   item(percent=20, member=True, scope="Änglamark")),
    (B, 1): answer("promotion", item("n_for_m", n=3, m=2, scope="fisk og skalldyr"),
                   item("amount_off", amount=10, member=True, scope="Coop pizza")),
    (A, 2): answer("offers"),
    (B, 2): "not json",
    (A, 3): answer("non_offer"),
    (B, 3): answer("non_offer"),
}


class SyntheticWeek(unittest.TestCase):
    """A saved three-page run with answers from both models."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.run_dir = self.tmp / "2026-W44" / "run-20261026T060000Z"
        pages = []
        for n in (1, 2, 3):
            image = self.run_dir / f"pages/d1/cat/p{n:03d}.jpg"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b"\xff\xd8\xff" + bytes([n]))
            pages.append({"catalog_id": "cat", "page": n, "file": f"pages/d1/cat/p{n:03d}.jpg",
                          "sha256": hashlib.sha256(image.read_bytes()).hexdigest()})
        (self.run_dir / "d1.pages.json").write_text(json.dumps({"pages": pages}))
        (self.run_dir / "d1.hotspots.json").write_text(json.dumps({"catalogs": [
            {"catalog_id": "cat", "hotspot_offer_pages": {"o1": [2]}}]}))
        (self.run_dir / "d1.json").write_text(json.dumps({"entries": [{"catalog": {
            "id": "cat", "label": "Uke 44", "dealer": {"name": "Coop Prix"},
            "run_from": "2026-10-25T23:00:00+0000", "run_till": "2026-11-01T22:59:59+0000"}}]}))
        self.out = self.tmp / "out"
        self.db = self.tmp / "offers.sqlite3"
        self.run_id = "2026-W44/run-20261026T060000Z"

    def execute(self, answers=ANSWERS):
        from nutrivalue.page_sets import run_pages
        run(run_pages(self.run_dir), list(PAIR), out_dir=self.out, cap_usd=Decimal("1"),
            catalogue=CATALOGUE, client=FakeClient(answers), execute=True, sleep=lambda s: None)

    def register_run(self):
        with sqlite3.connect(self.db) as db:
            for name in ("tjek_offer_staging.sql", "offer_database.sql"):
                db.executescript((ROOT / "analytics" / name).read_text())
            db.execute("INSERT INTO tjek_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (self.run_id, "2026-W44", "2026-10-26T06:00:00+00:00",
                        "2026-10-26T07:00:00+01:00", "a" * 64, "complete", None, 40, 1, 900,
                        "2026-10-26T07:00:00+00:00"))

    def lookup(self):
        from nutrivalue.page_sets import run_pages
        return OcrLookup(run_pages(self.run_dir), self.out / "ocr",
                         reader=lambda path, cache: ("fake", ["3 for 2", "-20 %"]))


class ExtractionTests(SyntheticWeek):
    def test_build_accepts_agreement_and_queues_the_rest(self):
        self.execute()
        built = build(self.run_dir, self.out, ocr_lookup=self.lookup())
        ext = built["extraction"]
        self.assertEqual(ext["extraction_id"], extraction_id(self.run_id))
        self.assertEqual((ext["pages"], ext["invalid_pages_b"], ext["agreed_items"],
                          ext["disagreeing_items"]), (3, 1, 1, 2))
        self.assertEqual(ext["cost_usd"], "0.0030")
        self.assertEqual(json.loads(ext["providers"])[A], {"Anthropic": 3})
        promos = {(r[4], r[12]): r for r in built["promotions"]}
        accepted = promos[("n_for_m", "accepted")]
        self.assertEqual((accepted[10], accepted[11], accepted[14], accepted[15]),
                         ("Plukk & miks fisk", "fisk og skalldyr", "confirmed", METHOD))
        self.assertEqual(promos[("amount_off", "needs_review")][8], 1000)   # øre
        self.assertEqual(json.loads(promos[("percent_off", "needs_review")][13]), [A])
        status = {r[2]: r[11] for r in built["pages"]}
        self.assertEqual(status, {1: "ok", 2: "needs_review", 3: "ok"})
        self.assertEqual(ext["ocr_engine"], "fake")
        self.assertIn("| Coop Prix | 3 | 1 | 1 | 0 | 0 | 0 | 2 |", report(built))

    def test_import_views_idempotence_and_immutability(self):
        self.execute()
        self.register_run()
        built = build(self.run_dir, self.out, ocr_lookup=self.lookup())
        self.assertTrue(import_extraction(built, self.db, ROOT / "analytics")["imported"])
        self.assertFalse(import_extraction(built, self.db, ROOT / "analytics")["imported"])
        with sqlite3.connect(self.db) as db:
            rows = db.execute("SELECT week, chain_name, kind, status, valid_from "
                              "FROM v_week_promotions ORDER BY item_no").fetchall()
            self.assertEqual(rows[0], ("2026-W44", "Coop Prix", "n_for_m", "accepted",
                                       "2026-10-25T23:00:00+0000"))
            self.assertEqual(len(rows), 3)
            review = db.execute("SELECT item, page FROM v_week_promotion_review "
                                "ORDER BY item, page").fetchall()
            self.assertEqual(review, [("page", 2), ("promotion", 1), ("promotion", 1)])
            health = db.execute("SELECT pages, review_queue_size, agreement_rate "
                                "FROM v_promotion_monitoring").fetchone()
            self.assertEqual(health, (3, 3, 0.333))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM promo_answers").fetchone(), (6,))
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE page_promotions SET status='accepted'")
        changed = build(self.run_dir, self.out)
        changed["extraction"]["results_sha256"] = "b" * 64
        with self.assertRaises(ExtractionError):
            import_extraction(changed, self.db, ROOT / "analytics")

    def test_incomplete_or_foreign_answers_fail_closed(self):
        with self.assertRaises(ExtractionError):        # nothing answered yet
            build(self.run_dir, self.out)
        self.execute()
        path = result_path(self.out, "", B, "cat", 3)
        saved = json.loads(path.read_text())
        path.write_text(json.dumps(dict(saved, prompt_version="page_promotions@1")))
        with self.assertRaises(ExtractionError):
            build(self.run_dir, self.out)
        path.write_text(json.dumps(dict(saved, image_sha256="c" * 64)))
        with self.assertRaises(ExtractionError):
            build(self.run_dir, self.out)

    def test_run_must_be_imported_first(self):
        self.execute()
        with self.assertRaises(ExtractionError):
            import_extraction(build(self.run_dir, self.out), self.db, ROOT / "analytics")


class ReviewTests(SyntheticWeek):
    """The owner's review of the queue (inherits the synthetic week)."""

    def setUp(self):
        super().setUp()
        self.execute()
        self.register_run()
        import_extraction(build(self.run_dir, self.out, ocr_lookup=self.lookup()), self.db,
                          ROOT / "analytics")

    def final(self):
        with sqlite3.connect(self.db) as db:
            return dict(db.execute("SELECT kind, final_status FROM v_week_promotions"))

    def test_queue_lists_items_with_both_answers(self):
        week, items = review_items(self.db, ROOT / "analytics")
        self.assertEqual(week, "2026-W44")
        self.assertEqual([(i["item"], i["page"]) for i in items],
                         [("promotion", 1), ("promotion", 1), ("page", 2)])
        self.assertEqual(set(items[0]["answers"]), set(PAIR))
        self.assertIsNone(items[2]["answers"][B])
        self.assertEqual(describe(items[0]["promotion"]), "-20 % (members)")
        self.assertEqual(describe(items[1]["promotion"]), "-10 kr (members)")

    def test_decisions_are_appended_and_the_latest_wins(self):
        _, items = review_items(self.db, ROOT / "analytics")
        record_review(self.db, ROOT / "analytics", items[0], "reject", "app example")
        record_review(self.db, ROOT / "analytics", items[1], "accept")
        record_review(self.db, ROOT / "analytics", items[2], "page_checked")
        self.assertEqual(review_items(self.db, ROOT / "analytics")[1], [])
        self.assertEqual(self.final(), {"n_for_m": "accepted", "percent_off": "rejected",
                                        "amount_off": "accepted_by_review"})
        record_review(self.db, ROOT / "analytics", items[0], "accept")
        self.assertEqual(self.final()["percent_off"], "accepted_by_review")
        with self.assertRaises(ExtractionError):
            record_review(self.db, ROOT / "analytics", items[2], "accept")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM promo_reviews").fetchone(), (4,))
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM promo_reviews")

    def test_page_image_is_found_only_when_unchanged(self):
        sha = hashlib.sha256((self.run_dir / "pages/d1/cat/p001.jpg").read_bytes()).hexdigest()
        self.assertEqual(page_image(self.tmp, self.run_id, "cat", 1, sha),
                         self.run_dir / "pages/d1/cat/p001.jpg")
        self.assertIsNone(page_image(self.tmp, self.run_id, "cat", 1, "0" * 64))
        self.assertIsNone(page_image(self.tmp / "gone", self.run_id, "cat", 1, sha))

    def test_terminal_review_flow(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import review_promotions
        answers = iter(["x", "a", "r", "not in this store", "c"])
        shown, printed = [], []
        db = self.db
        original = review_promotions.PRIVATE
        review_promotions.PRIVATE = self.tmp      # allow the temporary database
        try:
            code = review_promotions.main(
                ["--db", str(db), "--weekly-root", str(self.tmp)],
                ask=lambda prompt: next(answers), show=shown.append, out=printed.append)
        finally:
            review_promotions.PRIVATE = original
        self.assertEqual(code, 0)
        self.assertEqual(len(shown), 3)
        self.assertIn("Saved 3 decision(s). Open items left: 0", printed[-1])
        self.assertEqual(self.final(), {"n_for_m": "accepted", "percent_off": "accepted_by_review",
                                        "amount_off": "rejected"})


class CliTests(unittest.TestCase):
    def test_paths_must_stay_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(
                [sys.executable, str(ROOT / "scripts/extract_week_promotions.py"),
                 "--run-dir", f"{tmp}/2026-W44/run-x", "--import-only"],
                capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("data/private", done.stderr)


if __name__ == "__main__":
    unittest.main()
