"""TASK-032: synthetic zero-HTTP immutable replay after Coop PDF bug fixes."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from nutrivalue.providers.tjek_extra_official import replay_saved_official
from nutrivalue.providers.tjek_weekly_collect import CollectionError

AT = "2026-10-15T12:00:00+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-19T00:00:00+02:00"
IDS = ("test-ost-a", "test-vest-b", "test-nord-c")
REGION_WEEK = "EXTRA\nØST\nPrisene gjelder uke 42 - 2026\n"


def offer(cid, suffix, name):
    return {
        "id": f"{cid}-{suffix}", "catalog_id": cid, "dealer_id": "80742m",
        "heading": name, "description": "SYNTHETIC TEST FOOD",
        "pricing": {"price": 29.90, "currency": "NOK"},
        "quantity": {"unit": {"symbol": "g", "si": {"symbol": "kg", "factor": .001}},
                     "size": {"from": 500, "to": 500}, "pieces": None},
        "run_from": START, "run_till": END,
    }


class OfflineReplayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.folder = self.root / "2026-W42" / "extra-compare-20261016T004643Z"
        self.folder.mkdir(parents=True)
        self.origin = {"status": "region_unresolved", "reason": "old parser bug"}
        (self.folder / "official-ost-match.json").write_text(json.dumps(self.origin))
        (self.folder / "coop-ost.pdf").write_bytes(b"%PDF-fixture-no-network")
        self.attempts = []
        for cid in IDS:
            rows = [offer(cid, "shared", "COMMON FOOD ITEM")]
            if cid == IDS[0]:
                rows += [offer(cid, f"unique-{n}", f"EAST SPECIAL PRODUCT {n}")
                         for n in range(4)]
            envelope = {
                "fetched_at": AT,
                "entries": [{
                    "catalog": {"id": cid, "dealer_id": "80742m",
                                "label": "", "run_from": START,
                                "run_till": END, "offer_count": len(rows)},
                    "offers": rows,
                }],
            }
            raw = json.dumps(envelope, sort_keys=True).encode("utf-8")
            (self.folder / f"{cid}.json").write_bytes(raw)
            self.attempts.append({
                "catalog_id": cid, "status": "source_saved",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "available_offers": len(rows), "shortfall": 15,
            })
        (self.folder / "manifest.json").write_text(json.dumps({
            "week": "2026-W42", "run_directory": self.folder.name,
            "halted_reason": None, "attempts": self.attempts,
        }))

    def replay(self, text=REGION_WEEK, **kwargs):
        return replay_saved_official(
            compare_dir=self.folder, private_root=self.root,
            as_of=AT, extract_text=lambda _: text, **kwargs,
        )

    def test_success_with_existing_pdfs_creates_new_report_without_changes(self):
        text = REGION_WEEK + "\f".join(
            f"EAST SPECIAL PRODUCT {n} 29 90" for n in range(4))
        existing = (self.folder / "official-ost-match.json").read_bytes()
        original_pdf = (self.folder / "coop-ost.pdf").read_bytes()
        result = self.replay(text=text)
        self.assertEqual(result["status"], "ost_content_correlated")
        self.assertEqual(result["candidate_for_ost"], IDS[0])
        self.assertTrue(result["original_report_preserved"])
        self.assertEqual(result["tjek_http_requests"], 0)
        self.assertEqual(result["coop_http_requests"], 0)
        self.assertEqual(result["source_declared_offer_shortfall_sum"], 45)
        self.assertEqual((self.folder / "official-ost-match.json").read_bytes(), existing)
        self.assertEqual((self.folder / "coop-ost.pdf").read_bytes(), original_pdf)
        self.assertTrue((self.folder / "official-ost-match-replay-v2.json").exists())
        with self.assertRaisesRegex(CollectionError, "already exists"):
            self.replay(text=text)

    def test_rejected_official_text_is_unresolved_not_false_success(self):
        result = self.replay(text="EXTRA ØST uke 42 - 2025")
        self.assertEqual(result["status"], "region_unresolved")
        self.assertIsNone(result["candidate_for_ost"])
        self.assertIn("official_pdf_validation_or_text_failure",
                      result["unresolved_reason"])
        self.assertFalse(result["official_text_region_and_week_verified"])

    def test_tied_candidate_evidence_reports_insufficient(self):
        result = self.replay(text=REGION_WEEK)
        self.assertEqual(result["status"], "region_unresolved")
        self.assertIsNone(result["candidate_for_ost"])
        self.assertEqual(result["unresolved_reason"],
                         "insufficient_or_tied_distinctive_offer_evidence")
        self.assertTrue(result["official_text_region_and_week_verified"])

    def test_changed_checksum_fails_before_any_replay_output(self):
        altered = self.folder / f"{IDS[0]}.json"
        altered.write_bytes(altered.read_bytes() + b"\n")
        with self.assertRaisesRegex(CollectionError, "digest or size mismatch"):
            self.replay()
        self.assertFalse((self.folder / "official-ost-match-replay-v2.json").exists())

    def test_private_archive_traversal_and_missing_pdf_rejected(self):
        with self.assertRaisesRegex(CollectionError, "outside private"):
            replay_saved_official(compare_dir=self.root.parent,
                                 private_root=self.root, as_of=AT)
        (self.folder / "coop-ost.pdf").unlink()
        with self.assertRaisesRegex(CollectionError, "missing or symlinked"):
            self.replay()


if __name__ == "__main__":
    unittest.main()
