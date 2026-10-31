"""TASK-024: weekly pipeline, GCS state sync and the Cloud Run Job entry point.

Everything is offline: a fake collector writes a synthetic run, a fake model
client answers, and an in-memory store stands in for GCS.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from nutrivalue.cloud_sync import DB_OBJECT, SyncError, pull, push
from nutrivalue.standing_benefits import load_registry
from nutrivalue.weekly_pipeline import WeeklyRunError, paths, run_week
from test_promotion_extraction import A, B, CATALOGUE, ROOT, answer, item

sys.path.insert(0, str(ROOT / "scripts"))
import cloud_weekly_job  # noqa: E402

REGISTRY = load_registry(ROOT / "data" / "reference" / "standing_benefits.json")


class Client:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def complete(self, model, prompt, image, schema, *, structured, temperature, max_tokens,
                 provider=None):
        self.calls.append((model, image[-1]))
        if "checks" in schema.get("properties", {}):
            ids = [int(line.split(". ", 1)[0]) for line in prompt.splitlines()
                   if line[:1].isdigit() and ". " in line]
            content = json.dumps({"checks": [{"id": i, "printed": False, "member_only": False,
                                              "evidence": ""} for i in ids]})
        else:
            content = self.answers[(model, image[-1])]
        return {"content": content, "cost_usd": 0.0004, "prompt_tokens": 4000,
                "completion_tokens": 100, "provider": "Anthropic" if model == A else "OpenAI"}


ANSWERS = {(A, 1): answer("promotion", item("n_for_m", n=3, m=2, scope="Fisk")),
           (B, 1): answer("promotion", item("n_for_m", n=3, m=2, scope="Fisk og skalldyr")),
           (A, 2): answer("offers"), (B, 2): answer("offers"),
           (A, 3): answer("offers", item(percent=20, scope="Jif")),
           (B, 3): answer("offers")}


def fake_collector(images: dict, week="2026-W44", halted=None):
    """collect(out_dir, allow_repeat) writing a minimal run with page images."""
    def collect(out_dir, allow_repeat):
        week_dir = Path(out_dir) / week
        earlier = list(week_dir.glob("run-*/manifest.json"))
        if not allow_repeat and earlier:
            raise AssertionError("repeat without allow_repeat")
        run_dir = week_dir / f"run-2026102{len(earlier) + 1}T060000Z"
        run_dir.mkdir(parents=True)
        pages = []
        for number, data in images.items():
            image = run_dir / f"pages/d1/cat/p{number:03d}.jpg"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(data)
            pages.append({"catalog_id": "cat", "page": number,
                          "file": f"pages/d1/cat/p{number:03d}.jpg",
                          "sha256": hashlib.sha256(data).hexdigest()})
        (run_dir / "d1.pages.json").write_text(json.dumps({"pages": pages}))
        (run_dir / "d1.hotspots.json").write_text(json.dumps({"catalogs": [
            {"catalog_id": "cat", "hotspot_offer_pages": {"o1": [2]}}]}))
        (run_dir / "d1.json").write_text(json.dumps({"entries": [{"catalog": {
            "id": "cat", "label": "Uke 44", "dealer": {"name": "Coop Prix"},
            "run_from": "2026-10-25T23:00:00+0000", "run_till": "2026-11-01T22:59:59+0000"}}]}))
        (run_dir / "manifest.json").write_text(json.dumps({"week": week}))
        return {"week": week, "run_directory": run_dir.name, "batch_status": "candidate_complete",
                "halted_reason": halted, "total_saved_offers": 10}
    return collect


def fake_import_run(run_dir, db_path, schema_dir):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        for name in ("tjek_offer_staging.sql", "offer_database.sql"):
            db.executescript((Path(schema_dir) / name).read_text())
        db.execute("INSERT INTO tjek_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (f"{run_dir.parent.name}/{run_dir.name}", run_dir.parent.name,
                    datetime.now().isoformat(), "x", "a" * 64, "ok", None, 40, 1, 10, "now"))
    return {"imported": True}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.images = {n: b"\xff\xd8\xff" + bytes([n]) for n in (1, 2, 3)}

    def week(self, mode, client, images=None):
        return run_week(self.root, mode=mode, collect=fake_collector(images or self.images),
                        catalogue=CATALOGUE, client=client, cap_usd=Decimal("1"),
                        registry=REGISTRY, schema_dir=ROOT / "analytics", sleep=lambda s: None,
                        ocr_reader=lambda path, cache: ("fake", ["3 for 2"]),
                        import_run_fn=fake_import_run)

    def test_monday_then_thursday_pays_only_for_changed_pages(self):
        first = Client(ANSWERS)
        monday = self.week("monday", first)
        self.assertEqual((monday["page_calls"], monday["review_queue"]), (6, 0))
        self.assertEqual(monday["crosscheck_calls"], 1)      # Jif -20 % only from A
        self.assertEqual(Decimal(monday["spent_usd"]), Decimal("0.0028"))   # 7 calls
        report = (self.root / monday["report"]).read_text()
        self.assertIn("Promotions 2026-W44/", report)
        changed = {**self.images, 2: b"\xff\xd8\xff\x99"}   # Jif page 3 unchanged
        answers = dict(ANSWERS)
        answers[(A, 0x99)], answers[(B, 0x99)] = answer("offers"), answer("offers")
        second = Client(answers)
        thursday = run_week(self.root, mode="thursday", collect=fake_collector(changed),
                            catalogue=CATALOGUE, client=second, cap_usd=Decimal("1"),
                            registry=REGISTRY, schema_dir=ROOT / "analytics",
                            sleep=lambda s: None,
                            ocr_reader=lambda path, cache: ("fake", ["3 for 2"]),
                            import_run_fn=fake_import_run)
        self.assertEqual(thursday["page_calls"], 2)             # only the changed page
        self.assertEqual(thursday["reused"]["answers"], 4)
        self.assertEqual((thursday["crosscheck_calls"], thursday["reused"]["crosschecks"]),
                         (0, 1))
        self.assertEqual(sorted(second.calls), [(A, 0x99), (B, 0x99)])
        self.assertEqual(Decimal(thursday["spent_usd"]), Decimal("0.0008"))
        self.assertGreater(Decimal(thursday["answers_cost_usd"]), Decimal(thursday["spent_usd"]))
        with sqlite3.connect(paths(self.root)["db"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM promo_extractions").fetchone(),
                             (2,))

    def test_halted_collector_stops_after_import_and_bad_mode(self):
        with self.assertRaises(WeeklyRunError):
            run_week(self.root, mode="monday", collect=fake_collector(self.images,
                                                                      halted="HTTP 429"),
                     catalogue=CATALOGUE, client=Client(ANSWERS), cap_usd=Decimal("1"),
                     registry=REGISTRY, schema_dir=ROOT / "analytics",
                     import_run_fn=fake_import_run)
        with self.assertRaises(WeeklyRunError):
            self.week("sunday", Client(ANSWERS))


class FakeStore:
    """In-memory GCS with generations and preconditions."""

    def __init__(self):
        self.objects, self.counter = {}, 0

    def generation(self, name):
        return self.objects.get(name, (None, 0))[1]

    def names(self, prefix):
        return sorted(n for n in self.objects if n.startswith(prefix))

    def download(self, name, path, generation=None):
        data, gen = self.objects[name]
        if generation is not None and generation != gen:
            raise RuntimeError("precondition failed")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def upload(self, path, name, if_generation_match):
        if self.generation(name) != if_generation_match:
            raise RuntimeError(f"precondition failed for {name}")
        self.counter += 1
        self.objects[name] = (Path(path).read_bytes(), self.counter)

    def put(self, name, data):
        self.counter += 1
        self.objects[name] = (data, self.counter)

    def read_bytes(self, name, generation=None):
        data, gen = self.objects[name]
        if generation is not None and generation != gen:
            raise RuntimeError("precondition failed")
        return data

    def create(self, name, data, content_type):
        if name in self.objects:
            raise RuntimeError(f"precondition failed for {name}")
        self.put(name, data)


class SyncTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = FakeStore()

    def test_pull_needs_db_and_mirrors_config_and_week(self):
        with self.assertRaises(SyncError):
            pull(self.store, self.root, "2026-W44")
        self.assertEqual(pull(self.store, self.root, "2026-W44", allow_empty_db=True),
                         {"db_generation": 0, "archive_files": 0, "review_files": 0})
        self.store.put(DB_OBJECT, b"db")
        self.store.put("config/2026-W42/extra-api-investigation/oslo_stores.json", b"[]")
        self.store.put("archive/weekly/2026-W44/run-x/manifest.json", b"{}")
        self.store.put("archive/promotions/2026-W44/run-x/ocr/a.json", b"{}")
        self.store.put("archive/weekly/2026-W43/run-y/manifest.json", b"{}")
        pulled = pull(self.store, self.root, "2026-W44")
        self.assertEqual(pulled["archive_files"], 2)
        private = self.root / "data/private"
        self.assertEqual((private / "offers.sqlite3").read_bytes(), b"db")
        self.assertTrue((private / "tjek/weekly/2026-W42/extra-api-investigation/"
                                   "oslo_stores.json").exists())
        self.assertFalse((private / "tjek/weekly/2026-W43").exists())
        self.store.put("archive/weekly/2026-W44/../../evil", b"x")
        with self.assertRaises(SyncError):
            pull(self.store, self.root, "2026-W44")

    def test_push_is_create_only_and_db_generation_checked(self):
        private = self.root / "data/private"
        run_dir = private / "tjek/weekly/2026-W44/run-a"
        (run_dir / "pages/d/c").mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{}")
        (run_dir / "pages/d/c/p001.jpg").write_bytes(b"img")
        (private / "tjek/promotions/2026-W44/run-a/ocr").mkdir(parents=True)
        (private / "tjek/promotions/2026-W44/run-a/ocr/c_p001.json").write_text("{}")
        (private / "tjek/reports/2026-W44").mkdir(parents=True)
        (private / "tjek/reports/2026-W44/run-a.md").write_text("# r")
        (private / "offers.sqlite3").write_bytes(b"new db")
        self.store.put(DB_OBJECT, b"old db")
        result = push(self.store, self.root, "2026-W44", "run-a", self.store.generation(DB_OBJECT))
        self.assertEqual(result["uploaded_files"], 4)
        self.assertIn("page-images/2026-W44/run-a/pages/d/c/p001.jpg", self.store.objects)
        self.assertIn("archive/weekly/2026-W44/run-a/manifest.json", self.store.objects)
        self.assertIn("archive/reports/2026-W44/run-a.md", self.store.objects)
        self.assertEqual(self.store.objects[DB_OBJECT][0], b"new db")
        with self.assertRaises(RuntimeError):          # archive objects are never replaced
            push(self.store, self.root, "2026-W44", "run-a", self.store.generation(DB_OBJECT))
        other = FakeStore()
        other.put(DB_OBJECT, b"changed meanwhile")
        with self.assertRaises(RuntimeError):          # stale database generation
            push(other, self.root, "2026-W44", "run-a", 0)


class JobTests(unittest.TestCase):
    NOW = datetime(2026, 10, 26, 7, 45, tzinfo=ZoneInfo("Europe/Oslo"))   # Monday, W44

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = {"NV_BUCKET": "b", "NV_WORKSPACE": str(Path(tmp.name) / "ws")}
        self.store = FakeStore()
        self.store.put(DB_OBJECT, b"")

    def main(self, run_week_fn, **env):
        def collect_week_fn(**kwargs):
            return fake_collector({1: b"\xff\xd8\xff\x01"})(kwargs["out_dir"],
                                                            kwargs["allow_repeat"])
        return cloud_weekly_job.main({**self.env, **env}, store=self.store, now=self.NOW,
                                     collect_week_fn=collect_week_fn, run_week_fn=run_week_fn,
                                     models=CATALOGUE, client=Client(ANSWERS))

    def test_auto_mode_upload_and_thursday_detection(self):
        seen = []

        def fake_run_week(root, *, mode, collect, **kw):
            seen.append(mode)
            collected = collect(paths(root)["weekly"], mode == "thursday")
            run = paths(root)["weekly"] / collected["week"] / collected["run_directory"]
            db = paths(root)["db"]
            db.write_bytes(db.read_bytes() + b"+")
            return {"week": collected["week"], "run": run.name}

        self.assertEqual(self.main(fake_run_week), 0)
        self.assertEqual(seen, ["monday"])
        self.assertEqual(self.store.objects[DB_OBJECT][0], b"+")
        self.assertTrue(self.store.names("archive/weekly/2026-W44/"))
        self.assertTrue(self.store.names("page-images/2026-W44/"))
        self.assertEqual(self.main(fake_run_week), 0)
        self.assertEqual(seen, ["monday", "thursday"])
        self.assertEqual(self.store.objects[DB_OBJECT][0], b"++")

    def test_failure_still_uploads_what_was_collected(self):
        def failing(root, *, mode, collect, **kw):
            collect(paths(root)["weekly"], False)
            raise RuntimeError("budget cap would be exceeded; stop")

        self.assertEqual(self.main(failing), 1)
        self.assertTrue(self.store.names("archive/weekly/2026-W44/"))

    def test_configuration_errors(self):
        self.assertEqual(cloud_weekly_job.main({}, store=self.store), 1)
        self.assertEqual(self.main(lambda *a, **k: {}, NV_BUDGET_USD="50"), 1)
        self.assertEqual(self.main(lambda *a, **k: {}, NV_MODE="friday"), 1)
        self.assertEqual(self.main(lambda *a, **k: {}, NV_EXTRA_STORE_NAME="Extra Theresesgate"),
                         1)
        self.store.objects.pop(DB_OBJECT)                 # no database in the bucket yet
        self.assertEqual(self.main(lambda *a, **k: {}), 1)


if __name__ == "__main__":
    unittest.main()
