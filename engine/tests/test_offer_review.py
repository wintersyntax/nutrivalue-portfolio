"""Synthetic only: private human review sampling and tiny local baseline."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nutrivalue.offer_review import (
    ReviewError, aggregate_review, make_label, private_labels_write,
    sample_archives,
)
from nutrivalue.offer_ml import evaluate_labels, load_private_labels, NgramNB
from nutrivalue.providers.tjek_offline import CHAINS

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/review_offer_sample.py"
NOW = "2026-10-17T12:00:00+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-18T23:59:59+02:00"


class PrivateReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / "archive"
        self.archive.mkdir()
        self.private = self.root / "data" / "private"
        self.private.mkdir(parents=True)

    def one(self, dealer: str, title: str, index: int) -> dict:
        return {
            "id": "synthetic-id-" + str(index), "dealer_id": dealer,
            "catalog_id": "synthetic-catalog-" + dealer,
            "heading": title, "description": "",
            "pricing": {"currency": "NOK", "price": 39.90},
            "run_from": START, "run_till": END,
        }

    def create(self, dealer, titles):
        content = {
            "fetched_at": "2026-10-16T10:00:00+02:00",
            "catalog": {
                "id": "synthetic-catalog-" + dealer,
                "dealer_id": dealer, "label": "SYNTHETIC ONLY",
                "run_from": START, "run_till": END, "offer_count": len(titles),
            },
            "offers": [self.one(dealer, title, idx) for idx, title in enumerate(titles)],
        }
        path = self.archive / f"{dealer}.json"
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return path

    def test_all_nine_chains_and_status_buckets_repeatable_no_raw_report(self):
        paths = [self.create(d, ["LAKS", "RØKT LAKS",
                                 "UNRECOGNIZED-OWNER-SOURCE-TITLE"])
                 for d in CHAINS]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        selected, safe = sample_archives(paths, per_bucket=1)
        again, second_report = sample_archives(paths, per_bucket=1)
        self.assertEqual(selected, again)
        self.assertEqual(safe, second_report)
        self.assertEqual(len(selected), 27)
        self.assertEqual(safe["input_chains"], 9)
        self.assertEqual(safe["sample_size"], 27)
        self.assertEqual(len(safe["by_chain_and_status"]), 9)
        for chain in CHAINS.values():
            for status in ("matched", "needs_review", "unmapped"):
                self.assertEqual(safe["by_chain_and_status"][chain][status]["sampled"], 1)
        out = json.dumps(safe)
        for value in ("UNRECOGNIZED-OWNER-SOURCE-TITLE", "LAKS",
                      "synthetic-catalog", "synthetic-id", str(self.root)):
            self.assertNotIn(value, out)
        self.assertEqual(safe["network_requests"], 0)
        self.assertEqual(safe["database_writes"], 0)
        self.assertEqual(before, {
            p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
        })

    def test_duplicate_titles_in_one_chain_are_not_reviewed_twice(self):
        # Reviewer shouldn't get the same exact heading twice for that chain.
        paths = [self.create("faa0Ym", ["LAKS", "LAKS", "TORSK"])]
        samples, report = sample_archives(paths, per_bucket=5)
        self.assertEqual(len(samples), 2)
        self.assertEqual(report["by_chain_and_status"]["REMA 1000"]
                         ["matched"]["available_distinct_titles"], 2)

    def test_invalid_paths_duplicate_chain_and_parameter_rejected(self):
        a = self.create("faa0Ym", ["LAKS"])
        b = self.archive / "other.json"
        b.write_bytes(a.read_bytes())
        with self.assertRaisesRegex(ReviewError, "duplicate"):
            sample_archives([a, b])
        with self.assertRaises(ReviewError):
            sample_archives([a], per_bucket=0)
        with self.assertRaises(ReviewError):
            sample_archives([a], per_bucket=6)
        with self.assertRaises(ReviewError):
            sample_archives([self.archive / "missing.json"])
        with self.assertRaises(ReviewError):
            sample_archives([])

    def test_private_owner_labels_are_opt_in_exclusive_and_0600(self):
        source = self.create("faa0Ym", ["LAKS", "RØKT LAKS"])
        sample, report = sample_archives([source])
        labels = [make_label(item, "fish") for item in sample]
        path = self.private / "review-01.jsonl"
        self.assertFalse(path.exists())
        summary = aggregate_review(labels, sampled=report["sample_size"])
        private_labels_write(path, labels, private_root=self.private)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(len(load_private_labels(path, private_root=self.private)), 2)
        self.assertEqual(summary["reviewed"], 2)
        self.assertEqual(summary["rule_category_disagreements"], 0)
        self.assertNotIn("RØKT LAKS", json.dumps(summary, ensure_ascii=False))
        with self.assertRaisesRegex(ReviewError, "already exists"):
            private_labels_write(path, labels, private_root=self.private)
        other = self.root / "public.jsonl"
        with self.assertRaisesRegex(ReviewError, "ignored data/private"):
            private_labels_write(other, labels, private_root=self.private)
        self.assertFalse(other.exists())

    def test_conflicting_labels_for_same_title_fail_closed(self):
        p = self.private / "conflict.jsonl"
        p.write_text(
            json.dumps({"heading": "LAKS", "label": "fish", "chain": "KIWI"})
            + "\n" + json.dumps(
                {"heading": "Laks", "label": "meat", "chain": "REMA 1000"})
            + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "contradictory"):
            load_private_labels(p, private_root=self.private)

    def test_cli_shareable_plan_no_private_headings_and_no_writes(self):
        self.create("faa0Ym", ["SECRET-OWNER-TITLE-DO-NOT-PUBLISH",
                                "KYLLINGFILET", "PANERT TORSK"])
        before = sorted(p.name for p in self.archive.iterdir())
        call = subprocess.run([sys.executable, str(CLI), "--archive-dir",
                               str(self.archive), "--per-bucket", "1"],
                              text=True, capture_output=True, check=False)
        self.assertEqual(call.returncode, 0, call.stderr)
        result = json.loads(call.stdout)
        self.assertEqual(result["input_chains"], 1)
        self.assertEqual(result["sample_size"], 3)
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["database_writes"], 0)
        self.assertNotIn("SECRET-OWNER-TITLE", call.stdout + call.stderr)
        self.assertNotIn(str(self.archive), call.stdout + call.stderr)
        self.assertEqual(before, sorted(p.name for p in self.archive.iterdir()))

    def test_cli_non_tty_review_refuses_to_show_or_store_titles(self):
        self.create("faa0Ym", ["OWNER-PRIVATE-SECRET"])
        output = self.private / "should-not-exist.jsonl"
        result = subprocess.run([
            sys.executable, str(CLI),
            "--archive-dir", str(self.archive), "--review", "--labels", str(output)],
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("interactive terminal", result.stderr)
        self.assertFalse(output.exists())
        self.assertNotIn("OWNER-PRIVATE-SECRET", result.stdout + result.stderr)


class BaselineTests(unittest.TestCase):
    def test_held_out_title_groups_and_aggregate_model_comparison(self):
        # Explicitly SYNTHETIC, not a measurement on 863 real offers.
        fishes = ["LAKSEFILET", "TORSKEFILET", "TORSK", "SEIFILET",
                  "SILD", "REKER", "ØRRET", "SKREI"]
        meats = ["KYLLINGFILET", "KYLLING", "KARBONADEDEIG", "SVINEKOTELETT",
                 "LAMMELÅR", "BIFF", "KALKUNFILET", "SVINEFILET"]
        rows = (
            [{"heading": x, "label": "fish"} for x in fishes]
            + [{"heading": x, "label": "meat"} for x in meats]
            # Deliberate repeated normalized title: grouped, not leaked.
            + [{"heading": "laksefilet", "label": "fish"}]
        )
        result = evaluate_labels(rows)
        self.assertEqual(result["unique_owner_labeled_headings"], 16)
        self.assertEqual(result["train_groups"], 12)
        self.assertEqual(result["held_out_groups"], 4)
        self.assertEqual(result["rule_correct"], 4)
        self.assertIn("ml_correct", result)
        self.assertEqual(result["deployment_state"], "EXPERIMENT_ONLY_NOT_USED_FOR_AUTO_TAGGING")
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["file_writes"], 0)
        self.assertNotIn("LAKSEFILET", json.dumps(result))

    def test_experimental_model_requires_labels_and_never_mutates_input(self):
        examples = [{"heading": "LAKS", "label": "fish"},
                    {"heading": "KYLLING", "label": "meat"}]
        prior = json.dumps(examples)
        predictor = NgramNB(examples)
        self.assertIn(predictor.predict("TORSKEFILET"), {"fish", "meat"})
        self.assertEqual(prior, json.dumps(examples))
        with self.assertRaises(ReviewError):
            evaluate_labels(examples)

    def test_cli_evaluate_cannot_accept_data_outside_private(self):
        # A public path must be refused without revealing the secret source
        # heading or private input path. "PRIVATE" alone is not a suitable
        # leak sentinel: it is also in the CLI's generic error prefix.
        secret_heading = "SYNTHETIC-SECRET-HEADING-DO-NOT-ECHO"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "public.jsonl"
            path.write_text(json.dumps({
                "heading": secret_heading, "label": "fish", "chain": "KIWI",
            }) + "\n", encoding="utf-8")
            call = subprocess.run(
                [sys.executable, str(CLI), "--evaluate", "--labels", str(path)],
                capture_output=True, text=True, check=False)
            self.assertEqual(call.returncode, 2)
            self.assertIn("label file must remain inside ignored data/private",
                          call.stderr)
            self.assertNotIn(str(path), call.stdout + call.stderr)
            self.assertNotIn(secret_heading, call.stdout + call.stderr)
