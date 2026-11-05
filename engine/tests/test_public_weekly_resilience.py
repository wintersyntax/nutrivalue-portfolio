"""Portable, synthetic checks for the curated weekly and recipe boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from nutrivalue.page_sets import quarantined_chains, run_pages
from nutrivalue.weekly_pipeline import missing_chains
from test_promotion_extraction import ROOT, SyntheticWeek, build, import_extraction


class PublicWeeklyTests(SyntheticWeek):
    def test_quarantined_page_is_skipped_without_reading_its_context(self):
        (self.run_dir / "d2.unvalidated").write_text("{}")
        (self.run_dir / "d2.pages.json").write_text("not valid JSON")
        self.assertEqual(quarantined_chains(self.run_dir), ["d2"])
        self.assertEqual(len(run_pages(self.run_dir)), 3)

    def test_missing_store_and_rejections_stay_visible(self):
        result = missing_chains({"chains": [
            {"chain": "Synthetic A", "offers_saved": 0, "status": "error"},
            {"chain": "Synthetic B", "offers_saved": 2, "offers_rejected": 1,
             "rejected_reasons": ["invalid amount"]},
        ]})
        self.assertEqual(result["chains_missing_count"], 1)
        self.assertEqual(result["chains_missing"], "Synthetic A")
        self.assertEqual(result["offers_rejected"], 1)
        self.assertIn("invalid amount", result["offers_rejected_detail"])

    def test_later_run_without_extraction_keeps_earlier_promotions(self):
        import sqlite3

        self.execute()
        self.register_run()
        import_extraction(build(self.run_dir, self.out, ocr_lookup=self.lookup()),
                          self.db, ROOT / "analytics")
        with sqlite3.connect(self.db) as db:
            before = db.execute("SELECT * FROM v_week_promotions").fetchall()
            db.execute("INSERT INTO tjek_runs SELECT 'later', week, '2099-01-01', "
                       "as_of, manifest_sha256, batch_status, halted_reason, "
                       "http_requests, hotspots_enabled, total_saved_offers, imported_at "
                       "FROM tjek_runs LIMIT 1")
            self.assertEqual(db.execute("SELECT * FROM v_week_promotions").fetchall(), before)
        self.assertTrue(before)


class PublicMealieConfigurationTests(unittest.TestCase):
    def test_default_is_plan_only_and_execute_needs_explicit_address(self):
        env = dict(os.environ)
        env.pop("NV_MEALIE_URL", None)
        env.pop("MEALIE_API_TOKEN", None)
        command = [sys.executable, str(ROOT / "scripts/read_mealie_recipes.py")]
        plan = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("PLAN ONLY", plan.stdout)
        execute = subprocess.run(command + ["--execute"], env=env, capture_output=True, text=True)
        self.assertEqual(execute.returncode, 2)
        self.assertIn("requires --base-url", execute.stderr)


if __name__ == "__main__":
    unittest.main()
