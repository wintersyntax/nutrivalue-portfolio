"""TASK-032: synthetic nine-chain Tjek GET/archival tests; NO actual HTTP."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
import unittest
from pathlib import Path

from nutrivalue.providers.tjek_offline import CHAINS, parse_snapshot
from nutrivalue.providers.tjek_weekly_audit import audit_saved_week
from nutrivalue.providers.tjek_weekly_collect import (
    CollectionError, _live_catalogs, _oslo_geo_catalogs, OSLO_CITY_REFERENCE,
    collect_week, live_json, recover_quarantine,
)

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-10-15T12:00:00+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-19T00:00:00+02:00"
PUBLISHED = "2026-10-11T18:00:00+02:00"
RUN_ID = "20261015T120000Z"


def catalog(dealer, *, id=None, label="Uke 42", count=1, start=START, end=END):
    return {
        "id": id or f"c-{dealer}", "dealer_id": dealer,
        "label": label, "run_from": start, "run_till": end,
        "publish": PUBLISHED, "offer_count": count,
        "all_stores": False, "types": ["paged"],
    }


def offer(dealer, cat_id, *, id="o1", price=79.90):
    return {
        "id": id, "dealer_id": dealer, "catalog_id": cat_id,
        "heading": "SYNTHETIC FOOD", "description": "199,75 pr. kg",
        "pricing": {"price": price, "currency": "NOK"},
        "quantity": {
            "unit": {"symbol": "g"}, "size": {"from": 100, "to": 100},
            "pieces": {"from": 4, "to": 4},
        },
        "run_from": START, "run_till": END,
    }


class FakeGetter:
    def __init__(self):
        self.calls = []
        self.catalogs = {
            dealer: [catalog(
                dealer, label=("Joker kundeavis 12.10.–17.10."
                               if dealer == "b3e8Fm" else "Uke 42"))]
            for dealer in CHAINS
        }
        self.offers = {f"c-{dealer}": [offer(dealer, f"c-{dealer}")]
                       for dealer in CHAINS}
        self.raise_at = None

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.raise_at and len(self.calls) == self.raise_at:
            raise CollectionError("Tjek HTTP 429; no retry")
        if endpoint == "catalogs":
            return self.catalogs[params["dealer_ids"]]
        if endpoint == "offers":
            off = self.offers[params["catalog_id"]]
            return off[params["offset"]:params["offset"] + params["limit"]]
        raise AssertionError(endpoint)


class WeeklyCollectorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "private"
        self.fake = FakeGetter()

    def collect(self, **kw):
        captured = kw.pop("captured_at", datetime.fromisoformat(
            "2026-10-15T12:01:00+02:00"))
        return collect_week(as_of=AT, out_dir=self.out, get_json=self.fake,
                            run_id=kw.pop("run_id", RUN_ID),
                            captured_at=captured, **kw)

    def run_dir(self):
        return self.out / "2026-W42" / f"run-{RUN_ID}"

    def test_nine_selected_chain_gets_and_immutable_source_only_snapshots(self):
        report = self.collect()
        self.assertEqual(report["http_requests"], 18)
        self.assertEqual(report["candidate_chains"], 9)
        self.assertEqual(report["batch_status"], "candidate_complete")
        self.assertEqual(len(report["chains"]), 9)
        self.assertEqual(set(row["dealer_id"] for row in report["chains"]), set(CHAINS))
        self.assertTrue(all(row["status"] == "candidate_source_count_match"
                            for row in report["chains"]))
        self.assertTrue(all(call[1].get("dealer_ids") in CHAINS
                            for call in self.fake.calls if call[0] == "catalogs"))
        for dealer in CHAINS:
            raw = (self.run_dir() / f"{dealer}.json").read_bytes()
            snap = parse_snapshot(raw, f"{dealer}.json")
            self.assertEqual(len(snap.offers), 1)
            self.assertEqual(snap.offers[0][-1], "source_only")
            self.assertEqual(os.stat(self.run_dir() / f"{dealer}.json").st_mode & 0o777, 0o600)
        manifest = json.loads((self.run_dir() / "manifest.json").read_text())
        self.assertNotIn("SYNTHETIC FOOD", json.dumps(manifest))
        self.assertNotIn("79.9", json.dumps(manifest))
        self.assertEqual(manifest["run_directory"], f"run-{RUN_ID}")

    def test_collector_saved_files_replay_in_task030_audit(self):
        self.collect()
        files = [self.run_dir() / f"{dealer}.json" for dealer in CHAINS]
        # Fake capture stamps NOW, not Oct 15; can only audit at/after fetch.
        fetched = json.loads((self.run_dir() / "manifest.json").read_text())["fetched_at"]
        report = audit_saved_week(files, as_of=fetched)
        self.assertEqual(report["required_chains"], 9)
        self.assertEqual(len(report["chains"]), 9)
        self.assertEqual(report["network_requests"], 0)

    def test_oslo_city_flyers_include_separate_all_stores_but_skip_joker_recipe(self):
        for dealer, local_id, extra_id in (
            ("f5d5lm", "prix-oslo", "prix-global"),
            ("2686gD", "matkroken-oslo", "matkroken-global"),
        ):
            local = catalog(dealer, id=local_id, label="")
            universal = catalog(dealer, id=extra_id, label="")
            universal["all_stores"] = True
            self.fake.catalogs[dealer] = [local, universal]
            self.fake.offers[local_id] = [offer(dealer, local_id)]
            self.fake.offers[extra_id] = [offer(dealer, extra_id)]
        joker = "b3e8Fm"
        newsletter = catalog(joker, id="joker-oslo",
                             label="Joker kundeavis 12.10.–17.10.")
        cookbook = catalog(joker, id="joker-recipes",
                           label="Godt til middag oktober, oppskriftshefte Joker")
        self.fake.catalogs[joker] = [newsletter, cookbook]
        self.fake.offers["joker-oslo"] = [offer(joker, "joker-oslo")]
        self.fake.offers["joker-recipes"] = [offer(joker, "joker-recipes")]

        report = self.collect()
        self.assertEqual(report["http_requests"], 20)
        self.assertEqual(report["candidate_chains"], 9)
        for dealer, expected in (
            ("f5d5lm", ["prix-oslo", "prix-global"]),
            ("b3e8Fm", ["joker-oslo"]),
            ("2686gD", ["matkroken-oslo", "matkroken-global"]),
        ):
            row = next(x for x in report["chains"] if x["dealer_id"] == dealer)
            self.assertEqual(row["catalogs_selected"], len(expected))
            self.assertEqual(row["offers_saved"], len(expected))
            self.assertEqual(row["oslo_city_reference_geo"]["catalog_ids"], expected)
            self.assertFalse(row["oslo_city_reference_geo"]["specific_shop_verified"])
            saved = json.loads((self.run_dir() / f"{dealer}.json").read_text())
            self.assertEqual([e["catalog"]["id"] for e in saved["entries"]], expected)
            requests = [params for endpoint, params in self.fake.calls
                        if endpoint == "catalogs" and params["dealer_ids"] == dealer]
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["r_lat"], OSLO_CITY_REFERENCE[0])
            self.assertEqual(requests[0]["r_lng"], OSLO_CITY_REFERENCE[1])
        self.assertNotIn(
            "joker-recipes",
            [params.get("catalog_id") for endpoint, params in self.fake.calls
             if endpoint == "offers"],
        )

    def test_oslo_geo_ambiguous_empty_and_malformed_catalogs_fail_closed(self):
        dealer = "f5d5lm"
        base = catalog(dealer, id="prix-oslo", label="")
        valid = _oslo_geo_catalogs([base], dealer,
                                   datetime.fromisoformat(AT))
        self.assertEqual([x["id"] for x in valid], ["prix-oslo"])
        cases = [
            [base, catalog(dealer, id="second", label="")],
            [base, dict(base)],
            [dict(base, all_stores=None)],
            [dict(base, types="paged")],
            [dict(base, offer_count=True)],
            [dict(base, dealer_id="other")],
            [dict(base, run_till=START)],
            [dict(base, run_till="2026-10-14T00:00:00+02:00")],
            [dict(base, publish="2026-10-18T00:00:00+02:00")],
            [dict(base, all_stores=True)],
        ]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(CollectionError):
                _oslo_geo_catalogs(body, dealer, datetime.fromisoformat(AT))

    def test_source_count_shortfall_is_visible_not_assumed_complete(self):
        dealer = next(iter(CHAINS))
        self.fake.catalogs[dealer][0]["offer_count"] = 99
        r = self.collect()
        row = r["chains"][0]
        self.assertEqual(row["status"], "source_count_shortfall")
        self.assertEqual(row["source_count_shortfall"], 98)
        self.assertEqual(row["offers_saved"], 1)
        self.assertEqual(r["batch_status"], "partial_or_unproven")

    def test_no_weekly_catalog_and_exclude_expired_future_seasonal(self):
        dealer = next(iter(CHAINS))
        self.fake.catalogs[dealer] = [
            catalog(dealer, id="seasonal", label="Sommermat"),
            catalog(dealer, id="expired", start="2026-10-02T00:00:00+02:00",
                    end="2026-10-11T00:00:00+02:00"),
            catalog(dealer, id="future", start="2026-10-19T00:00:00+02:00",
                    end="2026-10-26T00:00:00+02:00"),
        ]
        r = self.collect()
        self.assertEqual(r["chains"][0]["status"], "missing_weekly_catalog")
        self.assertEqual(r["chains"][0]["offers_saved"], 0)
        self.assertFalse((self.run_dir() / f"{dealer}.json").exists())
        self.assertEqual(r["http_requests"], 17)

    def test_pagination_100_then_2_and_no_off_by_one(self):
        dealer = next(iter(CHAINS))
        catid = f"c-{dealer}"
        self.fake.catalogs[dealer][0]["offer_count"] = 102
        self.fake.offers[catid] = [
            offer(dealer, catid, id=f"offer-{i}") for i in range(102)
        ]
        r = self.collect()
        self.assertEqual(r["http_requests"], 19)
        self.assertEqual(r["chains"][0]["offers_saved"], 102)
        self.assertEqual(r["chains"][0]["status"], "candidate_source_count_match")
        self.assertIn(("offers", {"catalog_id": catid, "limit": 100, "offset": 100}),
                      self.fake.calls)

    def test_one_full_page_with_page_cap_must_report_incomplete(self):
        dealer = next(iter(CHAINS))
        catid = f"c-{dealer}"
        self.fake.catalogs[dealer][0]["offer_count"] = 100
        self.fake.offers[catid] = [offer(dealer, catid, id=f"offer-{i}") for i in range(100)]
        r = self.collect(max_pages=1)
        self.assertEqual(r["chains"][0]["offers_saved"], 100)
        self.assertEqual(r["chains"][0]["status"], "offer_paging_capped")
        self.assertEqual(r["batch_status"], "partial_or_unproven")

    def test_catalog_selection_cap_is_reported(self):
        dealer = next(iter(CHAINS))
        c1 = catalog(dealer, id="one", label="Uke 42", count=2)
        c2 = catalog(dealer, id="two", label="Uke 42 Mat", count=1)
        self.fake.catalogs[dealer] = [c1, c2]
        self.fake.offers["one"] = [offer(dealer, "one")]
        self.fake.offers["two"] = [offer(dealer, "two")]
        r = self.collect(max_catalogs=1)
        self.assertEqual(r["chains"][0]["status"], "catalog_selection_capped")
        self.assertEqual(r["chains"][0]["catalogs_selected"], 1)

    def test_regional_duplicate_prefers_richest_weekly_catalog(self):
        dealer = next(iter(CHAINS))
        self.fake.catalogs[dealer] = [
            catalog(dealer, id="smaller", count=1),
            catalog(dealer, id="richer", count=3),
        ]
        self.fake.offers["richer"] = [offer(dealer, "richer")]
        r = self.collect()
        self.assertEqual(r["chains"][0]["catalogs_selected"], 1)
        self.assertEqual(r["chains"][0]["status"], "source_count_shortfall")
        first = parse_snapshot((self.run_dir() / f"{dealer}.json").read_bytes(),
                               f"{dealer}.json")
        self.assertEqual(first.catalogs[0][1], "richer")

    def test_http_429_fails_closed_and_marks_remaining_unattempted(self):
        self.fake.raise_at = 2
        r = self.collect()
        self.assertEqual(r["http_requests"], 2)
        self.assertEqual(r["chains"][0]["status"], "error")
        self.assertEqual(r["chains"][1]["status"], "not_attempted")
        self.assertEqual(r["halted_reason"], "Tjek HTTP 429; no retry")
        self.assertEqual(r["batch_status"], "partial_or_unproven")

    def test_local_parser_rejection_is_quarantined_and_other_eight_are_fetched(self):
        dealer = next(iter(CHAINS))
        cid = f"c-{dealer}"
        # This offer passes bounded provider-envelope/dealer validation, but
        # TASK-023 must reject EUR rather than silently treat it as NOK.
        self.fake.offers[cid][0]["pricing"]["currency"] = "EUR"
        report = self.collect()
        self.assertEqual(report["http_requests"], 18)
        self.assertEqual(report["chains"][0]["status"], "error")
        self.assertEqual(report["chains"][0]["offers_saved"], 0)
        self.assertEqual(report["chains"][0]["snapshot_file"], None)
        self.assertEqual(report["chains"][0]["quarantine_file"],
                         f"{dealer}.unvalidated")
        self.assertIn("only NOK prices accepted",
                      report["chains"][0]["validation_error"])
        self.assertIsNone(report["halted_reason"])
        self.assertEqual(report["candidate_chains"], 8)
        self.assertEqual(report["batch_status"], "partial_or_unproven")
        self.assertEqual(report["chains"][-1]["status"], "candidate_source_count_match")
        self.assertTrue((self.run_dir() / f"{dealer}.unvalidated").is_file())
        self.assertFalse((self.run_dir() / f"{dealer}.json").exists())
        report_text = (self.run_dir() / "manifest.json").read_text()
        self.assertNotIn("SYNTHETIC FOOD", report_text)
        self.assertNotIn('"EUR"', report_text)

    def test_quarantined_invalid_offer_is_never_importable_as_candidate(self):
        dealer = next(iter(CHAINS))
        cid = f"c-{dealer}"
        self.fake.offers[cid][0]["pricing"]["currency"] = "EUR"
        self.collect()
        from nutrivalue.providers.tjek_offline import TjekSnapshotError
        with self.assertRaises(TjekSnapshotError):
            parse_snapshot((self.run_dir() / f"{dealer}.unvalidated").read_bytes(),
                           f"{dealer}.json")
        self.assertTrue(all(
            self.run_dir().joinpath(f"{other}.json").is_file()
            for other in list(CHAINS)[1:]
        ))

    def test_bunnpris_blank_optional_description_succeeds_in_realistic_capture(self):
        dealer = "5b11sm"
        self.fake.offers[f"c-{dealer}"][0]["description"] = ""
        report = self.collect()
        self.assertEqual(report["candidate_chains"], 9)
        self.assertEqual(report["http_requests"], 18)
        first = report["chains"][0]
        self.assertEqual(first["status"], "candidate_source_count_match")
        self.assertEqual(first["offers_saved"], 1)
        self.assertIsNone(first["validation_error"])
        self.assertIsNone(first["quarantine_file"])
        saved = (self.run_dir() / f"{dealer}.json").read_bytes()
        parsed = parse_snapshot(saved, f"{dealer}.json")
        self.assertIsNone(parsed.offers[0][5])
        self.assertEqual(parsed.offers[0][17], "calculated_unchecked")
        self.assertEqual(parsed.offers[0][-1], "source_only")

    def test_quarantine_recovery_is_offline_dry_run_and_immutable(self):
        dealer = "5b11sm"
        folder = self.run_dir()
        folder.mkdir(parents=True, exist_ok=True)
        envelope = {
            "fetched_at": PUBLISHED,
            "entries": [{"catalog": catalog(dealer),
                         "offers": [offer(dealer, f"c-{dealer}")]}],
        }
        envelope["entries"][0]["offers"][0]["description"] = "   "
        original = json.dumps(envelope, sort_keys=True).encode("utf-8")
        quarantine = folder / f"{dealer}.unvalidated"
        quarantine.write_bytes(original)
        manifest = folder / "manifest.json"
        manifest.write_bytes(b'{"original_run": "failed"}')
        before_manifest = manifest.read_bytes()
        dry = recover_quarantine(archive_root=self.out, input_path=quarantine)
        self.assertEqual(dry["status"], "validated_dry_run")
        self.assertEqual(dry["offers"], 1)
        self.assertEqual(dry["http_requests"], 0)
        self.assertFalse((folder / f"{dealer}.json").exists())
        saved = recover_quarantine(archive_root=self.out,
                                   input_path=quarantine, apply=True)
        self.assertEqual(saved["status"], "validated_saved")
        self.assertEqual((folder / f"{dealer}.json").read_bytes(), original)
        self.assertEqual(quarantine.read_bytes(), original)
        self.assertEqual(manifest.read_bytes(), before_manifest)
        self.assertEqual(os.stat(folder / f"{dealer}.json").st_mode & 0o777, 0o600)
        self.assertEqual(parse_snapshot(original, f"{dealer}.json").offers[0][5], None)
        with self.assertRaisesRegex(CollectionError, "never overwrite"):
            recover_quarantine(archive_root=self.out,
                               input_path=quarantine, apply=True)

    def test_quarantine_recovery_rejects_corruption_and_external_paths(self):
        dealer = "5b11sm"
        folder = self.run_dir()
        folder.mkdir(parents=True, exist_ok=True)
        quarantine = folder / f"{dealer}.unvalidated"
        quarantine.write_bytes(b"{corrupted")
        with self.assertRaisesRegex(CollectionError, "still invalid"):
            recover_quarantine(archive_root=self.out, input_path=quarantine,
                               apply=True)
        self.assertFalse((folder / f"{dealer}.json").exists())
        outside = Path(self.out).parent / "not-private.unvalidated"
        outside.write_bytes(quarantine.read_bytes())
        with self.assertRaisesRegex(CollectionError, "not within"):
            recover_quarantine(archive_root=self.out, input_path=outside)
        self.assertEqual(quarantine.read_bytes(), b"{corrupted")

    def test_request_budget_is_never_exceeded(self):
        r = self.collect(max_requests=9)
        self.assertEqual(r["http_requests"], 9)
        self.assertEqual(len(self.fake.calls), 9)
        self.assertEqual(r["halted_reason"], "HTTP request budget exhausted")
        self.assertEqual(r["batch_status"], "partial_or_unproven")

    def test_invalid_foreign_offer_aborts_without_source_promotion(self):
        dealer = next(iter(CHAINS))
        other = list(CHAINS)[1]
        self.fake.offers[f"c-{dealer}"] = [offer(other, f"c-{dealer}")]
        r = self.collect()
        self.assertEqual(r["chains"][0]["status"], "error")
        self.assertIsNone(r["chains"][0]["snapshot_file"])
        self.assertEqual(r["chains"][1]["status"], "not_attempted")

    def test_partial_page_saved_after_later_network_failure(self):
        dealer = next(iter(CHAINS))
        cid = f"c-{dealer}"
        self.fake.catalogs[dealer][0]["offer_count"] = 101
        self.fake.offers[cid] = [offer(dealer, cid, id=f"offer-{i}") for i in range(101)]
        self.fake.raise_at = 3
        r = self.collect()
        self.assertEqual(r["chains"][0]["status"], "partial_error")
        self.assertEqual(r["chains"][0]["offers_saved"], 100)
        self.assertTrue((self.run_dir() / f"{dealer}.json").exists())
        self.assertEqual(r["chains"][1]["status"], "not_attempted")

    def test_repeat_requires_explicit_opt_in_and_never_overwrites(self):
        self.collect()
        previous = (self.run_dir() / "manifest.json").read_bytes()
        with self.assertRaisesRegex(CollectionError, "already attempted"):
            self.collect(run_id="20261015T120001Z")
        self.assertEqual(len(self.fake.calls), 18)
        next_report = self.collect(allow_repeat=True, run_id="20261015T120001Z")
        self.assertEqual(next_report["http_requests"], 18)
        self.assertEqual((self.run_dir() / "manifest.json").read_bytes(), previous)
        self.assertTrue((self.out / "2026-W42/run-20261015T120001Z/manifest.json").exists())

    def test_bad_arguments_fail_before_first_get_and_no_archive(self):
        for kw in ({"max_requests": 8}, {"max_requests": 101},
                   {"max_catalogs": 0}, {"max_pages": 9},
                   {"run_id": "../../unsafe"}):
            with self.subTest(kw=kw), self.assertRaises(CollectionError):
                self.collect(**kw)
        self.assertEqual(self.fake.calls, [])

    def test_catalog_response_limit_100_is_not_silently_complete(self):
        dealer = next(iter(CHAINS))
        self.fake.catalogs[dealer] = [
            catalog(dealer, id=f"c-{i}") for i in range(100)
        ]
        r = self.collect()
        self.assertIn("100-record cap", r["halted_reason"])
        self.assertEqual(r["chains"][0]["status"], "error")

    def test_cli_default_plan_is_no_network_and_no_files(self):
        command = [sys.executable, str(ROOT / "scripts/capture_tjek_weekly.py")]
        r = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NO HTTP / NO FILES WRITTEN", r.stdout)
        self.assertIn("Bunnpris", r.stdout)
        self.assertIn("REMA 1000", r.stdout)

    def test_invalid_live_json_type_and_size_are_rejected(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, size):
                return b"{" * (size + 1)
        with self.assertRaisesRegex(CollectionError, "byte limit"):
            live_json("catalogs", {"dealer_ids": next(iter(CHAINS)), "limit": 100},
                      interval=0, opener=lambda *a, **kw: Response())

    def test_saved_manifest_never_claims_local_shopping_price_proof(self):
        r = self.collect()
        self.assertEqual(r["scope"], "private nine-chain Tjek source_only")
        self.assertIn("never checked shop prices", r["disclaimer"])
        self.assertNotIn("verified", r["batch_status"])


if __name__ == "__main__":
    unittest.main()
