"""Synthetic offline TASK-002 evidence review tests; NO real prices or source data."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nutrivalue.sku_nutrition_review import parse_evidence, review
from scripts.review_sku_nutrition import ROOT, run


EAN = "4006381333931"  # Synthetic test identifier; not a real groceries claim.


def synthetic_evidence(**kwargs):
    data = dict(
        ean=EAN, brand="Synthetic", name="Synthetic cooked chicken 110g",
        provider_names=["Synthetic cooked chicken 110g Synthetic"],
        net_weight_g=110, protein_per_100g=20, kcal_per_100g=101,
        carbs_per_100g=0.9, nutrition_basis="per_100g_as_sold",
        source_url="https://manufacturer.example.test/label",
        checked_on="2026-10-11", reviewer_confirmed=True,
        reviewer_note="Human compared the fictional label and exact variant.",
    )
    data.update(kwargs)
    return data


def source_json(**kwargs):
    p = dict(
        ean=EAN, name="Synthetic cooked chicken 110g Synthetic",
        brand="Synthetic", weight=110, weight_unit="g",
        current_price=89.0,  # FICTIONAL; never ranking-eligible.
        nutrition=[
            dict(code="protein", amount=20, unit="g"),
            dict(code="energi_kcal", amount=104, unit="kcal"),
            dict(code="karbohydrater", amount=1.9, unit="g"),
        ],
    )
    p.update(kwargs)
    return p


def row(raw=None, **kwargs):
    x = dict(id=1, ean=EAN,
             product_name="Synthetic cooked chicken 110g Synthetic",
             brand="Synthetic", reported_weight_g=110,
             raw_payload_json=json.dumps(raw or source_json()))
    x.update(kwargs)
    return x


class ManufacturerReviewTests(unittest.TestCase):
    def test_minor_provider_differences_are_warning_not_rejection(self):
        r = review(parse_evidence(synthetic_evidence()), [row()])
        self.assertEqual((r.identity_status, r.nutrition_status,
                          r.overall_status, r.price_status),
                         ("verified", "verified_with_warning", "verified_with_warning",
                          "source_only"))
        self.assertIn("minor_carbs_difference_unverified_provider_basis", r.reasons)
        self.assertIn("minor_kcal_difference_unverified_provider_basis", r.reasons)

    def test_exact_reference_values_are_verified_nutrition_not_price(self):
        raw = source_json(nutrition=[
            dict(code="protein", amount=20, unit="g"),
            dict(code="energi_kcal", amount=101, unit="kcal"),
            dict(code="karbohydrater", amount=0.9, unit="g"),
        ])
        r = review(parse_evidence(synthetic_evidence()), [row(raw)])
        self.assertEqual(r.overall_status, "verified")
        self.assertEqual(r.reasons, ())
        self.assertEqual(r.price_status, "source_only")

    def test_manual_attestation_required(self):
        r = review(parse_evidence(synthetic_evidence(reviewer_confirmed=False)), [row()])
        self.assertEqual(r.overall_status, "needs_review")
        self.assertIn("manual_manufacturer_review_pending", r.reasons)

    def test_conflicting_ean_variant_brand_or_weight_blocks(self):
        fixtures = [
            row(raw=source_json(ean="0000000000000")),
            row(raw=source_json(name="Unrelated chicken product 110g")),
            row(raw=source_json(brand="Other")),
            row(product_name="Different brand 110g"),
            row(brand="Other"),
            row(raw=source_json(weight=200)),
            row(reported_weight_g=None),
        ]
        for bad in fixtures:
            with self.subTest(bad=bad):
                result = review(parse_evidence(synthetic_evidence()), [bad])
                self.assertEqual(result.overall_status, "needs_review")

    def test_missing_or_unusable_provider_mass_rejected(self):
        for payload in [source_json(weight_unit="ml"),
                        source_json(weight=None),
                        source_json(weight_unit="piece")]:
            with self.subTest(payload=payload):
                self.assertIn("package_mass_conflict_or_missing",
                              review(parse_evidence(synthetic_evidence()),
                                     [row(payload)]).reasons)

    def test_major_protein_and_energy_conflicts_block(self):
        for nutrition in [
            [dict(code="protein", amount=18, unit="g")],
            [dict(code="protein", amount=20, unit="mg")],
            [dict(code="protein", amount=20, unit="g"),
             dict(code="protein", amount=20, unit="g")],
            [dict(code="protein", amount=20, unit="g"),
             dict(code="energi_kcal", amount=145, unit="kcal")],
            [dict(code="protein", amount=20, unit="g"),
             dict(code="karbohydrater", amount=5, unit="g")],
        ]:
            with self.subTest(nutrition=nutrition):
                r = review(parse_evidence(synthetic_evidence()),
                           [row(source_json(nutrition=nutrition))])
                self.assertEqual(r.nutrition_status, "needs_review")

    def test_multi_store_same_sku_is_one_review(self):
        a = row()
        b = row(id=2)
        r = review(parse_evidence(synthetic_evidence()), [a, b])
        self.assertEqual(r.candidate_count, 2)
        self.assertEqual(json.loads(r.staging_ids_json), [1, 2])
        self.assertEqual(r.overall_status, "verified_with_warning")

    def test_missing_staging_cannot_be_verified(self):
        r = review(parse_evidence(synthetic_evidence()), [])
        self.assertEqual(r.overall_status, "needs_review")
        self.assertIn("no_matching_staging_product", r.reasons)

    def test_strict_basis_gtin_and_source_contract(self):
        variants = [
            dict(nutrition_basis="per_serving"),
            dict(ean="4006381333932"),
            dict(net_weight_g=-110),
            dict(protein_per_100g=101),
            dict(kcal_per_100g=-1),
            dict(carbs_per_100g=101),
            dict(source_url="http://example.test"),
            dict(checked_on="yesterday"),
            dict(reviewer_confirmed="yes"),
            dict(provider_names=[]),
        ]
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_evidence(synthetic_evidence(**bad))

    def test_unchecked_provider_nutrition_is_not_assumed_per_100g(self):
        r = review(parse_evidence(synthetic_evidence()), [row()])
        self.assertEqual(r.evidence.protein_per_100g, 20)
        self.assertTrue(all("unverified_provider_basis" in x
                            for x in r.reasons))


class PrivateReviewDatabaseTests(unittest.TestCase):
    def test_preview_apply_idempotence_and_no_curated_promotion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private.sqlite3"
            evidence = Path(folder) / "checked.json"
            with sqlite3.connect(path) as db:
                db.executescript((ROOT / "analytics/schema.sql").read_text())
                db.execute(
                    """INSERT INTO kassalapp_staging
                    (provider_product_id, ean, product_name, brand, store_code,
                     price_nok, reported_weight_g, protein_g_unverified_basis,
                     fetched_at, price_checked_at, review_status, review_notes,
                     source_ref, raw_payload_json)
                    VALUES ('1', ?, ?, 'Synthetic', 'FAKE', 89, 110, 20,
                            '2026-10-11', NULL, 'needs_review', 'synthetic',
                            'https://example.test', ?)""",
                    (EAN, "Synthetic cooked chicken 110g Synthetic",
                     json.dumps(source_json())),
                )
            evidence.write_text(json.dumps({"evidence": [synthetic_evidence()]}))
            self.assertEqual(run(path, evidence), (1, 0))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM sku_nutrition_reviews").fetchone()[0], 0)
            self.assertEqual(run(path, evidence, apply=True), (1, 1))
            self.assertEqual(run(path, evidence, apply=True), (1, 0))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute(
                    "SELECT overall_status, price_status FROM sku_nutrition_reviews"
                ).fetchall(), [("verified_with_warning", "source_only")])
                for table in ("products", "nutrition_profiles", "price_observations"):
                    self.assertEqual(db.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
                sql = (ROOT / "analytics/queries/sku_nutrition_evidence_review.sql").read_text()
                result = db.execute(sql).fetchone()
                self.assertEqual(result[0], EAN)
                self.assertEqual(result[6], "verified_with_warning")
                self.assertEqual(result[7], "source_only")
            altered = synthetic_evidence(reviewer_note="Fresh manual review, same label")
            evidence.write_text(json.dumps({"evidence": [altered]}))
            self.assertEqual(run(path, evidence, apply=True), (1, 1))

    def test_invalid_evidence_cannot_modify_db(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private.sqlite3"
            evidence = Path(folder) / "evidence.json"
            with sqlite3.connect(path) as db:
                db.executescript((ROOT / "analytics/schema.sql").read_text())
            evidence.write_text(json.dumps({"evidence": [synthetic_evidence(
                nutrition_basis="per_serving")]}))
            with self.assertRaises(ValueError):
                run(path, evidence, apply=True)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM sku_nutrition_reviews").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
