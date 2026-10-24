"""TASK-035: deterministic, offline GTIN and evidence-provenance regressions."""
import unittest

from nutrivalue.gtin import (
    GTINAssertion, assess_gtin_assertions, canonical_gtin14,
)
from nutrivalue.sku_nutrition_review import _gtin13


class GS1ValidationTests(unittest.TestCase):
    def test_each_standard_length_and_canonical_padding(self):
        examples = {
            "96385074": "00000096385074",        # GTIN-8
            "036000291452": "00036000291452",    # GTIN-12
            "4006381333931": "04006381333931",   # GTIN-13
            "04006381333931": "04006381333931",  # GTIN-14
        }
        for original, padded in examples.items():
            with self.subTest(original=original):
                self.assertEqual(canonical_gtin14(original), padded)
                self.assertEqual(len(padded), 14)

    def test_mod10_check_digit_is_validated_at_each_length(self):
        for valid in ("96385074", "036000291452", "4006381333931",
                      "04006381333931"):
            bad = valid[:-1] + str((int(valid[-1]) + 1) % 10)
            with self.subTest(valid=valid):
                self.assertIsNone(canonical_gtin14(bad))

    def test_invalid_structure_and_type_fail_closed(self):
        invalid = (
            None, False, True, 4006381333931, 4006381333931.0,
            b"4006381333931", "", "1234567", "123456789",
            "123456789012345", " 4006381333931", "4006381333931 ",
            "400638-1333931", "+4006381333931", "４００６３８１３３３９３１",
            "400638133393１", "400638133393\n1",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(canonical_gtin14(value))

    def test_leading_zeros_are_preserved_and_equal_forms_share_canonical(self):
        canonical = "00036000291452"
        self.assertEqual(canonical_gtin14("036000291452"), canonical)
        self.assertEqual(canonical_gtin14("0036000291452"), canonical)
        self.assertEqual(canonical_gtin14(canonical), canonical)
        self.assertNotEqual(canonical_gtin14("96385074"), canonical)

    def test_legacy_manufacturer_gtin13_contract_is_unchanged(self):
        self.assertTrue(_gtin13("4006381333931"))
        self.assertFalse(_gtin13("4006381333932"))
        self.assertFalse(_gtin13("036000291452"))
        self.assertFalse(_gtin13("04006381333931"))
        self.assertFalse(_gtin13(4006381333931))
        self.assertFalse(_gtin13(" 4006381333931"))


class TypedAssertionTests(unittest.TestCase):
    def assertion(self, value, kind="ean13", source="kassalapp",
                  source_path="$.product.ean"):
        return GTINAssertion(value, kind, source, source_path)

    def test_empty_sample_and_invalid_only_are_not_matches(self):
        empty = assess_gtin_assertions([])
        self.assertEqual(empty.status, "none")
        self.assertEqual(empty.distinct_gtin14, ())
        rejected = self.assertion("4006381333932")
        result = assess_gtin_assertions([rejected])
        self.assertEqual(result.status, "none")
        self.assertEqual(result.rejected, (rejected,))
        self.assertEqual(result.candidates, ())

    def test_explicit_identifier_type_and_provenance_are_preserved(self):
        a = self.assertion("4006381333931", "EAN-13", "kassalapp", "$.ean")
        b = self.assertion("04006381333931", "GTIN_14", "manufacturer", "$.gtin")
        result = assess_gtin_assertions([a, b])
        self.assertEqual(result.status, "candidate_only")
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.candidates[0].assertion, a)
        self.assertEqual(result.candidates[1].assertion, b)
        self.assertEqual(result.distinct_gtin14, ("04006381333931",))
        self.assertEqual(result.rejected, ())

    def test_different_valid_gtins_are_an_explicit_conflict(self):
        a = self.assertion("4006381333931")
        b = self.assertion("036000291452", "upc", "another_provider", "$.upc")
        result = assess_gtin_assertions((a, b))
        self.assertEqual(result.status, "conflict")
        self.assertEqual(len(result.distinct_gtin14), 2)
        self.assertEqual(len(result.candidates), 2)

    def test_internal_identifiers_are_not_gtin_evidence_even_if_numeric(self):
        for kind in ("sku", "id", "productCode", "internal_id", "", 123):
            a = self.assertion("4006381333931", kind)
            with self.subTest(kind=kind):
                result = assess_gtin_assertions([a])
                self.assertEqual(result.status, "none")
                self.assertEqual(result.rejected, (a,))

    def test_explicit_typed_lengths_must_match(self):
        examples = (("4006381333931", "upc"), ("036000291452", "ean13"),
                    ("96385074", "gtin14"), ("4006381333931", "gtin8"))
        for value, kind in examples:
            with self.subTest(kind=kind):
                a = self.assertion(value, kind)
                self.assertEqual(assess_gtin_assertions([a]).rejected, (a,))

    def test_supported_generic_typed_fields_are_candidates_not_proof(self):
        for kind in ("gtin", "ean", "barcode"):
            a = self.assertion("4006381333931", kind)
            with self.subTest(kind=kind):
                result = assess_gtin_assertions([a])
                self.assertEqual(result.status, "candidate_only")
                self.assertEqual(len(result.candidates), 1)

    def test_invalid_provenance_is_refused(self):
        for source, path in (("", "$.ean"), ("kassalapp", ""),
                             (None, "$.ean"), ("kassalapp", None)):
            with self.subTest(source=source, path=path):
                with self.assertRaises(ValueError):
                    assess_gtin_assertions([self.assertion(
                        "4006381333931", source=source, source_path=path)])

    def test_non_assertion_containers_and_objects_are_refused(self):
        with self.assertRaises(TypeError):
            assess_gtin_assertions(self.assertion("4006381333931"))
        with self.assertRaises(TypeError):
            assess_gtin_assertions([{"value": "4006381333931"}])

    def test_malformed_and_valid_candidates_stay_separate(self):
        good = self.assertion("4006381333931", source_path="$.ean")
        bad = self.assertion(4006381333931, source_path="$.numeric_bad")
        wrong_kind = self.assertion("4006381333931", kind="sku",
                                    source_path="$.sku")
        result = assess_gtin_assertions([good, bad, wrong_kind])
        self.assertEqual(result.status, "candidate_only")
        self.assertEqual(tuple(x.assertion for x in result.candidates), (good,))
        self.assertEqual(result.rejected, (bad, wrong_kind))


if __name__ == "__main__":
    unittest.main()
