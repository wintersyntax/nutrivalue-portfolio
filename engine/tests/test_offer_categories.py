"""TASK-041: offline, synthetic-only Norwegian offer classification regressions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nutrivalue.offer_categories import CATEGORIES, RULES, TAXONOMY_VERSION, classify_offer
from nutrivalue.offer_category_audit import (
    OfferCategoryAuditError, audit_saved_offers,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "analytics/tjek_offer_staging.sql"
CLI = ROOT / "scripts/audit_offer_categories.py"
AS_OF = "2026-10-17T12:00:00+02:00"
START = "2026-10-12T00:00:00+02:00"
END = "2026-10-18T23:59:59+02:00"


class ClassifierTests(unittest.TestCase):
    def test_taxonomy_version_and_broad_category_coverage(self):
        self.assertEqual(TAXONOMY_VERSION, 6)
        self.assertEqual(set(RULES), CATEGORIES)
        for category, groups in RULES.items():
            self.assertTrue(groups, category)
            self.assertTrue(all(aliases for _, aliases in groups))

    def test_safe_food_type_matches_do_not_claim_sku_identity(self):
        cases = {
            "LAKSEFILET 400 G": ("fish", "salmon"),
            "TORSKEFILETER FRA NORGE": ("fish", "cod"),
            "SEIFILET 500 G": ("fish", "saithe"),
            "KYLLINGFILET FERSK": ("meat", "chicken_breast"),
            "KARBONADEDEIG 400 G": ("meat", "lean_beef_mince"),
            "SKYR NATURELL": ("dairy", "skyr"),
            "RIS 1 KG": ("grain", "rice"),
            "SPINAT": ("produce", "spinach"),
            "PEANØTTSMØR": ("pantry", "peanut_butter"),
            "FROSSENPIZZA": ("frozen", None),
            "FERDIGRETT": ("prepared", None),
            "ENERGIDRIKK": ("drink", None),
            "TOALETTPAPIR": ("non_food", None),
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.status, "matched")
                self.assertEqual((result.food_category, result.food_key), expected)
                self.assertEqual(result.taxonomy_version, TAXONOMY_VERSION)

    def test_prepared_and_smoked_types_cannot_be_approved_as_raw_food(self):
        samples = (
            "RØKT LAKS", "PANERT TORSK", "MARINERT KYLLINGFILET",
            "FISKEKAKER", "FISKEGRATENG", "LAKS PÅLEGG",
            "GRILLET TORSK", "LAKS KRYDRET",
        )
        for title in samples:
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.status, "needs_review")
                # Processed-food keys do not identify raw foods.
                self.assertEqual(
                    result.food_key,
                    {
                        "RØKT LAKS": "smoked_salmon",
                        "FISKEKAKER": "fish_cakes",
                    }.get(title),
                )
                self.assertEqual(result.reason, "preparation_or_form")
                self.assertEqual(result.form, "processed")

    def test_mixed_species_and_mixed_food_categories_need_review(self):
        both_fish = classify_offer("LAKS OG TORSK")
        self.assertEqual(both_fish.status, "needs_review")
        self.assertEqual(both_fish.food_category, "fish")
        self.assertEqual(both_fish.reason, "multiple_food_keys")
        self.assertIsNone(both_fish.food_key)
        mixed = classify_offer("LAKS MED POTETER")
        self.assertEqual(mixed.status, "needs_review")
        self.assertIsNone(mixed.food_category)
        self.assertEqual(mixed.reason, "multiple_categories")

    def test_explicit_nonfood_cue_never_enters_fish_deals(self):
        for title in ("FISKEOLJE", "LAKS HUNDEMAT", "FISK OMEGA-3", "FISK KATTEMAT"):
            with self.subTest(title=title):
                result = classify_offer(title)
                self.assertEqual(result.food_category, "non_food")
                self.assertIsNone(result.food_key)

    def test_description_only_never_promotes_exact_food_key(self):
        result = classify_offer("DAGENS TILBUD", "Torsk 400 g, se betingelser")
        self.assertEqual(result.food_category, "fish")
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.food_key)
        self.assertEqual(result.reason, "description_only")
        # Do not let description-only incidental words override a clear title.
        direct = classify_offer("LAKSEFILET", "Serveres med poteter og saus")
        self.assertEqual((direct.status, direct.food_key), ("matched", "salmon"))

    def test_unknowns_remain_unmapped_without_loss_or_guessing(self):
        for title in ("UKENS KAMPANJE", "LAKSEOLJE", "SYNTHETIC-UNKNOWN"):
            result = classify_offer(title)
            self.assertEqual(result.status, "unmapped")
            self.assertIsNone(result.food_key)
            self.assertIsNone(result.food_category)

    def test_norwegian_unicode_and_whole_word_boundaries(self):
        self.assertEqual(classify_offer("   TORSKEFILET / LAKS  ").status,
                         "needs_review")
        self.assertEqual(classify_offer("FROSNE TORSKEFILETER").form, "frozen")
        self.assertEqual(classify_offer("MАКRELL").status, "unmapped")  # Cyrillic АК
        self.assertEqual(classify_offer("MAKRELL I TOMAT").food_key,
                         "mackerel_tomato")
        self.assertEqual(classify_offer("MAKRELL I TOMAT").status, "matched")
        self.assertIsNone(classify_offer("TUNFISK PÅ BOKS").food_key)
        self.assertEqual(classify_offer("VEGANSK FISK").reason,
                         "plant_based_imitation")

    def test_longest_phrase_shadows_only_overlapping_ingredient_words(self):
        canned = classify_offer("MAKRELL I TOMAT")
        self.assertEqual((canned.status, canned.food_category, canned.food_key),
                         ("matched", "fish", "mackerel_tomato"))
        # A second, distinct tomato ingredient must NOT be swallowed by
        # the longest named product phrase.
        with_vegetables = classify_offer("MAKRELL I TOMAT OG TOMAT")
        self.assertEqual(with_vegetables.status, "needs_review")
        self.assertEqual(with_vegetables.reason, "mixed_product_selection")
        self.assertIsNone(with_vegetables.food_category)
        with_second_fish = classify_offer("MAKRELL I TOMAT OG LAKS")
        self.assertEqual(with_second_fish.status, "needs_review")
        self.assertEqual(with_second_fish.reason, "multiple_food_keys")
        self.assertEqual(with_second_fish.food_category, "fish")
        self.assertIsNone(with_second_fish.food_key)

    def test_invalid_input_rejected_boundedly(self):
        for value in (None, 12, "", "\t", "x" * 401):
            with self.subTest(value=str(value)[:12]), self.assertRaises(ValueError):
                classify_offer(value)
        with self.assertRaises(ValueError):
            classify_offer("LAKS", description=3)
        with self.assertRaises(ValueError):
            classify_offer("LAKS", description="x" * 4001)


class StagingAuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "private-synthetic.sqlite3"
        with sqlite3.connect(self.db_path) as db:
            db.executescript(SCHEMA.read_text(encoding="utf-8"))
        self.add_snapshot("a" * 64, "2026-10-16T10:00:00+02:00")

    def add_snapshot(self, digest, fetched):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """INSERT INTO tjek_snapshots
                   (sha256,format,fetched_at,raw_payload,raw_bytes)
                   VALUES (?,'json',?,?,?)""",
                (digest, fetched, b"SYNTHETIC", len(b"SYNTHETIC")))
            db.execute(
                """INSERT INTO tjek_catalogs
                   (snapshot_sha256,catalog_id,dealer_id,chain_name,
                    run_from,run_till,claimed_offers)
                   VALUES (?,'synthetic-week-42','faa0Ym','REMA 1000',?,?,?)""",
                (digest, START, END, 10))

    def add_offer(self, name, *, digest="a" * 64, offer_id=None,
                  member=0, currency="NOK", description=None, confidence="source_print_consistent"):
        if offer_id is None:
            offer_id = "synthetic-" + str(sum(1 for _ in self.rows()))
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """INSERT INTO tjek_offers (
                   snapshot_sha256,catalog_id,offer_id,dealer_id,heading,description,
                   price_ore,currency,quantity_diagnostic,unit_price_ore,
                   unit_price_basis,unit_price_confidence,member_condition,
                   multibuy_condition,deposit_condition,run_from,run_till)
                   VALUES (?,'synthetic-week-42',?,'faa0Ym',?,?,
                           4990,?,'exact_quantity',9980,
                           'kg',?,?,0,0,?,?)""",
                (digest, offer_id, name, description, currency, confidence, member, START, END))

    def rows(self):
        with sqlite3.connect(self.db_path) as db:
            return db.execute("SELECT offer_id FROM tjek_offers").fetchall()

    def test_aggregate_counts_multiple_categories_and_unmatched(self):
        for title in ("LAKSEFILET", "RØKT LAKS", "LAKS OG TORSK",
                      "KYLLINGFILET", "SKYR", "TOALETTPAPIR",
                      "SYNTHETIC-SECRET-HEADING"):
            self.add_offer(title)
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        report = audit_saved_offers(self.db_path, as_of=AS_OF)
        self.assertEqual(report["distinct_observed_offers"], 7)
        self.assertEqual(report["active_offers"], 7)
        self.assertEqual(report["status"],
                         {"matched": 4, "needs_review": 2, "unmapped": 1})
        self.assertEqual(report["by_category"]["fish"], 3)
        self.assertEqual(report["by_category"]["unresolved"], 1)
        self.assertEqual(report["by_chain"]["REMA 1000"]["all"], 7)
        self.assertEqual(report["potential_food_key_types"], 4)
        # Two of seven offers are NOT eligible food categories: explicit
        # non-food toilet paper and the unknown source heading.
        self.assertEqual(report["unconditioned_source_consistent_kg_rate"], 4)
        self.assertEqual(report["network_requests"], 0)
        self.assertEqual(report["database_writes"], 0)
        self.assertEqual(before, hashlib.sha256(self.db_path.read_bytes()).hexdigest())
        text = json.dumps(report, ensure_ascii=False)
        for forbidden in ("SYNTHETIC-SECRET-HEADING", "LAKSEFILET", "4990",
                          "synthetic-week-42", str(self.db_path)):
            self.assertNotIn(forbidden, text)

    def test_latest_distinct_offer_snapshot_without_mutating_staging(self):
        self.add_offer("UKENT KAMPANJE", offer_id="reuse")
        self.add_snapshot("b" * 64, "2026-10-17T10:00:00+02:00")
        self.add_offer("TORSK", digest="b" * 64, offer_id="reuse")
        report = audit_saved_offers(self.db_path, as_of=AS_OF)
        self.assertEqual(report["snapshot_rows"], 2)
        self.assertEqual(report["distinct_observed_offers"], 1)
        self.assertEqual(report["by_category"]["fish"], 1)
        self.assertEqual(report["potential_food_key_types"], 1)

    def test_conditioned_source_unit_price_is_not_comparable(self):
        self.add_offer("TORSK", member=1)
        self.add_offer("LAKS", confidence="calculated_unverified")
        report = audit_saved_offers(self.db_path, as_of=AS_OF)
        self.assertEqual(report["conditionally_priced"], 1)
        self.assertEqual(report["unconditioned_source_consistent_kg_rate"], 0)

    def test_description_only_food_is_excluded_from_food_rate_analysis(self):
        self.add_offer("DAGENS TILBUD", description="Torsk")
        self.add_offer("SENSODYNE CLINICAL WHITE")
        self.add_offer("LAKS OG SÅPE")
        report = audit_saved_offers(self.db_path, as_of=AS_OF)
        self.assertEqual(report["distinct_observed_offers"], 3)
        self.assertEqual(report["unconditioned_source_consistent_kg_rate"], 0)

    def test_tag_coverage_counts_each_offer_once_and_each_tag_separately(self):
        self.add_offer("Granola/Kesam mager")
        self.add_offer("LAKSEFILET")
        self.add_offer("TOALETTPAPIR")
        report = audit_saved_offers(self.db_path, as_of=AS_OF)
        self.assertEqual(report["distinct_observed_offers"], 3)
        self.assertEqual(report["by_tag"], {
            "dairy": 1, "grain": 1, "fish": 1, "non_food": 1})
        self.assertEqual(report["tag_resolved_offers"], 3)
        self.assertEqual(report["multi_tag_offers"], 1)
        self.assertEqual(report["by_offer_kind"]["assortment"], 1)
        self.assertEqual(report["unconditioned_source_consistent_kg_rate"], 1)

    def test_empty_staging_is_observed_zero_not_full_market_coverage(self):
        report = audit_saved_offers(self.db_path, as_of=AS_OF)
        self.assertEqual(report["distinct_observed_offers"], 0)
        self.assertEqual(report["status"]["unmapped"], 0)
        self.assertIn("no nationwide", report["notice"].lower())

    def test_wrong_price_identity_or_schema_fails_closed(self):
        self.add_offer("TORSK")
        with sqlite3.connect(self.db_path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            # immutable rows cannot be mutated; invalid schema = separate DB.
        bad = self.db_path.with_name("not-a-tjek.sqlite3")
        sqlite3.connect(bad).close()
        with self.assertRaises(OfferCategoryAuditError):
            audit_saved_offers(bad, as_of=AS_OF)
        with patch("nutrivalue.offer_category_audit.CHAINS", {}):
            with self.assertRaises(OfferCategoryAuditError):
                audit_saved_offers(self.db_path, as_of=AS_OF)

    def test_missing_snapshot_join_never_silently_reduces_coverage(self):
        # Foreign key constraints can be disabled by a malformed private
        # database writer; a joined audit must not misreport an empty market.
        with sqlite3.connect(self.db_path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute(
                """INSERT INTO tjek_offers (
                   snapshot_sha256,catalog_id,offer_id,dealer_id,heading,
                   quantity_diagnostic,unit_price_confidence,
                   member_condition,multibuy_condition,deposit_condition,
                   run_from,run_till)
                   VALUES ('cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                           'absent-catalog','orphan','faa0Ym','TORSK',
                           'unknown','withheld',0,0,0,?,?)""",
                (START, END))
        with self.assertRaisesRegex(OfferCategoryAuditError, "unjoined offers"):
            audit_saved_offers(self.db_path, as_of=AS_OF)

    def test_missing_db_invalid_timestamp_and_cap_fail_closed(self):
        with self.assertRaises(OfferCategoryAuditError):
            audit_saved_offers(self.db_path.parent / "missing.sqlite3", as_of=AS_OF)
        with self.assertRaises(OfferCategoryAuditError):
            audit_saved_offers(self.db_path, as_of="2026-10-17T12:00:00")
        self.add_offer("LAKS")
        with patch("nutrivalue.offer_category_audit.MAX_ROWS", 0):
            with self.assertRaises(OfferCategoryAuditError):
                audit_saved_offers(self.db_path, as_of=AS_OF)

    def test_cli_produces_aggregate_only_json_with_no_network(self):
        self.add_offer("SYNTHETIC-PRIVATE-TITLE-DO-NOT-LOG")
        result = subprocess.run(
            [sys.executable, str(CLI), "--db", str(self.db_path), "--as-of", AS_OF],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["distinct_observed_offers"], 1)
        self.assertEqual(report["network_requests"], 0)
        self.assertNotIn("PRIVATE-TITLE", result.stdout)
        self.assertNotIn(str(self.db_path), result.stdout)
        self.assertFalse(result.stderr)

    def test_interactive_local_review_only_emits_decision_not_original_title(self):
        script = ROOT / "scripts/review_offer_heading.py"
        result = subprocess.run([sys.executable, str(script)],
                                input="RØKT LAKS\nSYNTHETIC-PRIVATE-DESCRIPTION\n",
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"status": "needs_review"', result.stdout)
        self.assertIn('"food_category": "fish"', result.stdout)
        self.assertNotIn("RØKT LAKS", result.stdout)
        self.assertNotIn("SYNTHETIC-PRIVATE-DESCRIPTION", result.stdout)
        self.assertFalse(result.stderr)

    def test_cli_missing_db_does_not_create_one(self):
        path = self.db_path.with_name("never-created.sqlite3")
        result = subprocess.run(
            [sys.executable, str(CLI), "--db", str(path), "--as-of", AS_OF],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(path.exists())
        self.assertNotIn(str(path), result.stderr)
