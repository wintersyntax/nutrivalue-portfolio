"""TASK-023 synthetic offline Tjek snapshots: no external data or API requests."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from nutrivalue.providers.tjek_offline import (
    CHAINS, TjekSnapshotError, parse_snapshot, stage_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "analytics/tjek_offer_staging.sql"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-19T00:00:00+02:00"
FETCHED = "2026-10-11T18:00:00+02:00"


def make_offer(offer_id="synthetic-1", dealer="faa0Ym", catalog_id="catalog-42"):
    return {
        "id": offer_id, "catalog_id": catalog_id, "dealer_id": dealer,
        "heading": "Synthetic chicken portions", "description": "pr. kg 199,75",
        "pricing": {"price": 79.90, "pre_price": None, "currency": "NOK"},
        "quantity": {
            "unit": {"symbol": "g", "si": {"symbol": "kg", "factor": 0.001}},
            "size": {"from": 100, "to": 100},
            "pieces": {"from": 4, "to": 4},
        },
        "run_from": START, "run_till": END,
    }


def make_entry(dealer="faa0Ym", catalog_id="catalog-42"):
    return {
        "catalog": {
            "id": catalog_id, "dealer_id": dealer, "label": "Synthetic week 42",
            "run_from": START, "run_till": END, "publish": FETCHED,
            "offer_count": 1,
        },
        "offers": [make_offer(dealer=dealer, catalog_id=catalog_id)],
    }


def json_bytes(entries=None):
    return json.dumps({"fetched_at": FETCHED, "entries": entries or [make_entry()]},
                      ensure_ascii=False, sort_keys=True).encode("utf-8")


def gz_bytes(entries=None):
    entries = entries or [make_entry()]
    lines = [json.dumps({"v": 1, "kind": "header", "synced_at": FETCHED,
                         "catalogs": len(entries),
                         "offers": sum(len(e["offers"]) for e in entries)})]
    for entry in entries:
        lines.append(json.dumps({"kind": "catalog", "catalog": entry["catalog"]}))
        for offer in entry["offers"]:
            lines.append(json.dumps({"kind": "offer",
                                     "catalog_id": entry["catalog"]["id"],
                                     "offer": offer}))
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


class TjekOfflineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.dbpath = self.folder / "private.sqlite3"

    def test_nine_explicit_chain_ids_and_scope_no_store_proof(self):
        self.assertEqual(len(CHAINS), 9)
        expected = {"5b11sm", "de79dm", "f5d5lm", "80742m", "b3e8Fm",
                    "257bxm", "2686gD", "4333pm", "faa0Ym"}
        self.assertEqual(set(CHAINS), expected)
        entries = [make_entry(dealer=dealer, catalog_id=f"catalog-{n}")
                   for n, dealer in enumerate(CHAINS)]
        snap = parse_snapshot(json_bytes(entries), "snapshot.json")
        self.assertEqual(len(snap.catalogs), 9)
        self.assertEqual(len(snap.offers), 9)
        self.assertTrue(all(row[-4:-1] == (None, None, None) for row in snap.offers))
        self.assertTrue(all(row[-1] == "source_only" for row in snap.offers))
        bad = make_entry("unknown")
        with self.assertRaisesRegex(TjekSnapshotError, "outside selected"):
            parse_snapshot(json_bytes([bad]), "snapshot.json")

    def test_float_noise_in_price_is_rounded_but_real_third_decimal_rejected(self):
        # Synthetic binary-float noise must not invalidate an otherwise exact price.
        entry = make_entry()
        entry["offers"][0]["pricing"].update(price=8.1, pre_price=12.340000000000002)
        row = parse_snapshot(json_bytes([entry]), "snapshot.json").offers[0]
        self.assertEqual((row[6], row[7]), (810, 1234))
        for bad in (12.345, 0.0, -1.0, "12.340000000000002"):
            with self.subTest(pre_price=bad):
                entry["offers"][0]["pricing"]["pre_price"] = bad
                with self.assertRaisesRegex(TjekSnapshotError, "max two decimals"):
                    parse_snapshot(json_bytes([entry]), "snapshot.json")

    def test_blank_optional_coop_catalog_label_is_normalized_only(self):
        for blank in ("", "   ", chr(9), None):
            with self.subTest(label=repr(blank)):
                entry = make_entry()
                entry["catalog"]["label"] = blank
                raw = json_bytes([entry])
                snap = parse_snapshot(raw, "synthetic.json")
                self.assertIsNone(snap.catalogs[0][4])
                self.assertEqual(snap.raw_payload, raw)
                self.assertEqual(len(snap.offers), 1)
        entry = make_entry()
        entry["catalog"]["label"] = {"unsupported": "object"}
        with self.assertRaisesRegex(TjekSnapshotError, "catalog.label"):
            parse_snapshot(json_bytes([entry]), "synthetic.json")

    def test_optional_blank_offer_description_is_null_but_required_fields_stay_strict(self):
        for empty in ("", "   ", chr(9) + chr(10), None):
            with self.subTest(description=repr(empty)):
                entry = make_entry()
                entry["offers"][0]["description"] = empty
                raw = json_bytes([entry])
                snap = parse_snapshot(raw, "snapshot.json")
                self.assertIsNone(snap.offers[0][5])
                self.assertEqual(snap.offers[0][17], "calculated_unchecked")
                self.assertEqual(snap.offers[0][-1], "source_only")
                self.assertEqual(snap.raw_payload, raw)
                self.assertEqual(snap.sha256, hashlib.sha256(raw).hexdigest())
        for bad in (42, {"text": "not allowed"}, "a" * 8001):
            with self.subTest(bad=type(bad).__name__):
                entry = make_entry()
                entry["offers"][0]["description"] = bad
                with self.assertRaisesRegex(TjekSnapshotError, "offer.description"):
                    parse_snapshot(json_bytes([entry]), "snapshot.json")
        entry = make_entry()
        entry["offers"][0]["heading"] = "   "
        with self.assertRaisesRegex(TjekSnapshotError, "offer.heading"):
            parse_snapshot(json_bytes([entry]), "snapshot.json")

    def test_multipack_and_printed_rate_remain_source_only(self):
        snap = parse_snapshot(json_bytes(), "snapshot.json")
        self.assertEqual(snap.sha256, hashlib.sha256(json_bytes()).hexdigest())
        row = snap.offers[0]
        self.assertEqual(row[6], 7990)
        self.assertEqual((row[9], row[10], row[11]), ("100", "g", 4))
        self.assertEqual((row[13], row[14], row[15], row[16]),
                         (19975, "kg", 19975, 19975))
        self.assertEqual(row[17], "source_print_consistent")
        self.assertEqual(row[-1], "source_only")

    def test_upstream_gzip_ndjson_archive_roundtrip(self):
        raw = gz_bytes()
        snap = parse_snapshot(raw, "week.ndjson.gz")
        self.assertEqual(snap.format, "kundeavis_ndjson_gz")
        self.assertEqual(snap.raw_payload, raw)
        self.assertEqual(len(snap.offers), 1)
        self.assertEqual(snap.offers[0][13], 19975)
        with self.assertRaisesRegex(TjekSnapshotError, "header counts"):
            bogus = gzip.compress(gzip.decompress(raw).replace(
                b'"offers": 1', b'"offers": 2'))
            parse_snapshot(bogus, "week.ndjson.gz")
        orphan = [
            json.dumps({"v": 1, "kind": "header", "synced_at": FETCHED,
                        "catalogs": 0, "offers": 1}),
            json.dumps({"kind": "offer", "catalog_id": "absent",
                        "offer": make_offer()}),
        ]
        with self.assertRaisesRegex(TjekSnapshotError, "orphan"):
            parse_snapshot(gzip.compress(("\n".join(orphan) + "\n").encode()),
                           "week.ndjson.gz")

    def test_private_idempotent_database_and_immutable_snapshots(self):
        snap = parse_snapshot(json_bytes(), "snapshot.json")
        self.assertEqual(stage_snapshot(snap, self.dbpath, SCHEMA), (1, True))
        self.assertEqual(stage_snapshot(snap, self.dbpath, SCHEMA), (1, False))
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tjek_snapshots").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tjek_offers").fetchone()[0], 1)
            raw, digest, origin = db.execute(
                "SELECT raw_payload,sha256,origin_family FROM tjek_snapshots").fetchone()
            self.assertEqual((raw, digest, origin), (json_bytes(), snap.sha256, "tjek"))
            state, store, ean, nutrient = db.execute(
                "SELECT review_state,local_store_id,ean,nutrition_basis FROM tjek_offers").fetchone()
            self.assertEqual((state, store, ean, nutrient),
                             ("source_only", None, None, None))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE tjek_offers SET heading='tampered'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM tjek_snapshots")

    def test_new_week_keeps_past_snapshot(self):
        first = parse_snapshot(json_bytes(), "old.json")
        newer = make_entry()
        newer["catalog"]["id"] = "catalog-43"
        newer["catalog"]["run_from"] = END
        newer["catalog"]["run_till"] = "2026-10-26T00:00:00+01:00"
        newer["offers"][0]["id"] = "synthetic-2"
        newer["offers"][0]["catalog_id"] = "catalog-43"
        newer["offers"][0]["run_from"] = END
        newer["offers"][0]["run_till"] = "2026-10-26T00:00:00+01:00"
        second = parse_snapshot(json_bytes([newer]), "new.json")
        stage_snapshot(first, self.dbpath, SCHEMA)
        stage_snapshot(second, self.dbpath, SCHEMA)
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tjek_snapshots").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tjek_offers").fetchone()[0], 2)

    def test_member_multibuy_pant_withhold_unit_rate(self):
        for desc, expected in [
            ("pr. kg 199,75 Medlemspris", "withheld_membership"),
            ("2 for 159,80 pr. kg 199,75", "withheld_multibuy"),
            ("Pant 2 NOK pr. kg 199,75", "withheld_deposit"),
        ]:
            with self.subTest(desc=desc):
                entry = make_entry()
                entry["offers"][0]["description"] = desc
                row = parse_snapshot(json_bytes([entry]), "test.json").offers[0]
                self.assertIsNone(row[13])
                self.assertEqual(row[15], 19975)
                self.assertEqual(row[17], expected)

    def test_source_print_mismatch_is_not_used(self):
        entry = make_entry()
        entry["offers"][0]["description"] = "pr. kg 99,00"
        row = parse_snapshot(json_bytes([entry]), "test.json").offers[0]
        self.assertIsNone(row[13])
        self.assertEqual(row[15], 19975)
        self.assertEqual(row[16], 9900)
        self.assertEqual(row[17], "source_rate_mismatch")

    def test_ranged_missing_unsupported_or_si_conflict_withholds_unit_price(self):
        for change, diagnostic in [
            ({"size": {"from": 100, "to": 200}}, "ambiguous_size"),
            ({"pieces": {"from": 3, "to": 4}}, "ambiguous_pieces"),
            ({"unit": {"symbol": "pcs"}}, "unsupported_unit"),
            ({"unit": {"symbol": "g", "si": {"symbol": "kg", "factor": 1}}},
             "si_unit_conflict"),
        ]:
            with self.subTest(diagnostic=diagnostic):
                entry = make_entry()
                entry["offers"][0]["quantity"].update(change)
                row = parse_snapshot(json_bytes([entry]), "test.json").offers[0]
                self.assertIsNone(row[13])
                self.assertEqual(row[12], diagnostic)

    def test_unknown_price_and_broken_currency_fail_closed(self):
        entry = make_entry()
        entry["offers"][0]["pricing"]["price"] = None
        row = parse_snapshot(json_bytes([entry]), "test.json").offers[0]
        self.assertIsNone(row[6])
        self.assertEqual(row[17], "missing_or_ambiguous_price")
        for bad in (0, -1, 10.001, True, "nan", "free"):
            case = deepcopy(entry)
            case["offers"][0]["pricing"]["price"] = bad
            with self.subTest(bad=bad), self.assertRaises(TjekSnapshotError):
                parse_snapshot(json_bytes([case]), "test.json")
        entry["offers"][0]["pricing"]["currency"] = "EUR"
        with self.assertRaisesRegex(TjekSnapshotError, "NOK"):
            parse_snapshot(json_bytes([entry]), "test.json")

    def test_mismatched_dealer_catalog_offer_and_duplicate_identity(self):
        entry = make_entry()
        entry["offers"][0]["dealer_id"] = "257bxm"
        with self.assertRaisesRegex(TjekSnapshotError, "dealer does not match"):
            parse_snapshot(json_bytes([entry]), "test.json")
        entry = make_entry()
        entry["offers"].append(deepcopy(entry["offers"][0]))
        with self.assertRaisesRegex(TjekSnapshotError, "duplicate offer"):
            parse_snapshot(json_bytes([entry]), "test.json")
        entry = make_entry()
        entry["offers"][0]["catalog_id"] = "different"
        with self.assertRaisesRegex(TjekSnapshotError, "catalog id mismatch"):
            parse_snapshot(json_bytes([entry]), "test.json")

    def test_missing_timezone_invalid_utf8_and_unsupported_snapshot(self):
        entry = make_entry()
        entry["catalog"]["run_from"] = "2026-10-12"
        with self.assertRaisesRegex(TjekSnapshotError, "timezone-aware"):
            parse_snapshot(json_bytes([entry]), "test.json")
        with self.assertRaises(TjekSnapshotError):
            parse_snapshot(b"\xff", "test.json")
        with self.assertRaises(TjekSnapshotError):
            parse_snapshot(json_bytes(), "snapshot.csv")
        with self.assertRaises(TjekSnapshotError):
            parse_snapshot(b"", "snapshot.json")
        with self.assertRaises(TjekSnapshotError):
            parse_snapshot(b"\x1f\x8bcorrupt", "snapshot.ndjson.gz")

    def test_source_print_suffix_and_before_unit_formats(self):
        for desc in ("199,75 pr. kg", "199,75/KG"):
            with self.subTest(description=desc):
                entry = make_entry()
                entry["offers"][0]["description"] = desc
                offer = parse_snapshot(json_bytes([entry]), "test.json").offers[0]
                self.assertEqual((offer[13], offer[15], offer[16]),
                                 (19975, 19975, 19975))
                self.assertEqual(offer[17], "source_print_consistent")

    def test_offer_heading_promotion_restrictions(self):
        entry = make_entry()
        entry["offers"][0]["heading"] = "Synthetic 2 for price"
        entry["offers"][0]["description"] = "pr. kg 199,75"
        row = parse_snapshot(json_bytes([entry]), "test.json").offers[0]
        self.assertIsNone(row[13])
        self.assertEqual(row[17], "withheld_multibuy")

    def test_numbered_multibuy_heading_or_description_withholds_rate(self):
        for heading, description in (
            ("Synthetic chicken 2 for 159,80", "pr. kg 199,75"),
            ("Synthetic chicken 2 stk for", "pr. kg 199,75"),
            ("Synthetic chicken", "2 for 159,80 pr. kg 199,75"),
        ):
            with self.subTest(heading=heading, description=description):
                entry = make_entry()
                entry["offers"][0]["heading"] = heading
                entry["offers"][0]["description"] = description
                row = parse_snapshot(json_bytes([entry]), "test.json").offers[0]
                self.assertIsNone(row[13])
                self.assertEqual(row[17], "withheld_multibuy")
                self.assertEqual(row[19], 1)

    def test_non_multibuy_word_does_not_trigger_restriction(self):
        entry = make_entry()
        entry["offers"][0]["heading"] = "Synthetic chicken for hverdag"
        entry["offers"][0]["description"] = "pr. kg 199,75"
        row = parse_snapshot(json_bytes([entry]), "test.json").offers[0]
        self.assertEqual(row[13], 19975)
        self.assertEqual(row[17], "source_print_consistent")
        self.assertEqual(row[19], 0)

    def test_expired_offer_is_retained_but_not_claimed_current(self):
        entry = make_entry()
        entry["offers"][0]["run_from"] = "2026-09-01T00:00:00+02:00"
        entry["offers"][0]["run_till"] = "2026-09-08T00:00:00+02:00"
        snap = parse_snapshot(json_bytes([entry]), "test.json")
        self.assertEqual(snap.offers[0][21:23],
                         ("2026-09-01T00:00:00+02:00", "2026-09-08T00:00:00+02:00"))
        self.assertEqual(snap.offers[0][-1], "source_only")
        # Parsing a historical source is allowed; calling it a current store
        # price would require a separate date-filtered query and review.

    def test_cli_regular_import_is_private_and_idempotent(self):
        source = self.folder / "saved-private.json"
        source.write_bytes(json_bytes())
        command = [sys.executable, str(ROOT / "scripts/collect_tjek_offline.py"),
                   "--input", str(source), "--db", str(self.dbpath)]
        for attempt in range(2):
            p = subprocess.run(command, capture_output=True, text=True,
                               cwd=ROOT, timeout=10)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("No API requests", p.stdout)
            if attempt == 1:
                self.assertIn("replay", p.stdout)
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tjek_offers").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tjek_snapshots").fetchone()[0], 1)

    def test_cli_dry_run_never_creates_database_or_http_call(self):
        source = self.folder / "private.json"
        source.write_bytes(json_bytes())
        process = subprocess.run(
            [sys.executable, str(ROOT / "scripts/collect_tjek_offline.py"),
             "--input", str(source), "--db", str(self.dbpath), "--dry-run"],
            capture_output=True, text=True, cwd=ROOT, timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("DRY-RUN", process.stdout)
        self.assertIn("No API requests", process.stdout)
        self.assertFalse(self.dbpath.exists())


if __name__ == "__main__":
    unittest.main()
