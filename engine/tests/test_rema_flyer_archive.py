"""Source-only integrity checks for REMA research without private fixtures."""
from __future__ import annotations
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "research" / "rema_flyer_2026"

class TestRemaArchive(unittest.TestCase):
    def test_manifest(self):
        data = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["python_files"], 32)
        names = [row["path"] for row in data["files"]]
        self.assertEqual(len(names), len(set(names)))
        for row in data["files"]:
            path = ROOT / row["path"]
            self.assertTrue(path.is_file(), row["path"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["sha256"])
    def test_no_raw_source_fixtures(self):
        for path in ROOT.rglob("*"):
            if path.is_file() and not "__pycache__" in path.parts:
                self.assertFalse(path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".tsv", ".pdf", ".zip"}, path)
                if path.suffix == ".json": self.assertEqual(path.name, "source_manifest.json")
    def test_weekly_repeat_policy(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Repeated promotions are valid W43 offers", readme)
        self.assertIn("NOT_MEASURED_REVIEW_INCOMPLETE", readme)

if __name__ == "__main__": unittest.main()
