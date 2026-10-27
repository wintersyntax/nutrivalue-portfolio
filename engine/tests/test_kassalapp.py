import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from nutrivalue.providers.kassalapp import (
    KassalappClient, KassalappError, KassalappRateLimit, stage_product,
)
from scripts.collect_kassalapp import collect


def synthetic_product(**kwargs):
    """Entirely fictional record; never real grocery prices."""
    base = {
        "id": 81234,
        "name": "Synthetic protein test food",
        "ean": "1234567890123",
        "brand": "Synthetic",
        "store": {"name": "Example", "code": "TEST_STORE"},
        "current_price": 60,
        "weight": 400,
        "weight_unit": "g",
        "nutrition": [{"code": "protein", "display_name": "Protein", "unit": "g", "amount": 18}],
    }
    base.update(kwargs)
    return base


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.data).encode()


class KassalappAdapterTests(unittest.TestCase):
    def test_bearer_search_path_and_parameters(self):
        seen = []
        def opener(req, timeout):
            seen.append((req, timeout))
            return FakeResponse({"data": [synthetic_product()]})
        api = KassalappClient("test-token", opener=opener)
        data = api.search_products("kylling filet", size=2)
        self.assertEqual(len(data["data"]), 1)
        self.assertIn("search=kylling+filet", seen[0][0].full_url)
        self.assertIn("size=2", seen[0][0].full_url)
        self.assertNotIn("exclude_without_ean", seen[0][0].full_url)
        self.assertIn("page=1", seen[0][0].full_url)
        self.assertEqual(seen[0][0].get_header("Authorization"), "Bearer test-token")
        self.assertEqual(seen[0][1], 20)

    def test_live_verified_search_parameters_and_no_ean_review(self):
        seen = []
        def opener(req, timeout):
            seen.append(req)
            # An unidentifiable product is not discarded just because
            # the API's optional EAN filter is omitted.
            return FakeResponse({"data": [synthetic_product(ean=None)]})
        result = KassalappClient("test-token", opener=opener).search_products(
            "kyllingfilet", size=20,
        )
        self.assertEqual(seen[0].full_url,
                         "https://kassal.app/api/v1/products?search=kyllingfilet&size=20&page=1")
        staged = stage_product(result["data"][0])
        self.assertEqual(staged.review_status, "incomplete")
        self.assertIsNone(staged.ean)
        self.assertIn("ean", staged.review_notes)

    def test_bulk_history_contract(self):
        seen = []
        def opener(req, timeout):
            seen.append(req)
            return FakeResponse({"data": [], "meta": {"requested_eans": 1}})
        result = KassalappClient("fake", opener=opener).bulk_price_history(["1234567890123"], days=30)
        self.assertEqual(result["data"], [])
        self.assertEqual(seen[0].get_method(), "POST")
        self.assertEqual(json.loads(seen[0].data)["eans"], ["1234567890123"])

    def test_429_does_not_retry(self):
        count = [0]
        def opener(req, timeout):
            count[0] += 1
            raise HTTPError(req.full_url, 429, "rate limit", {}, io.BytesIO())
        with self.assertRaises(KassalappRateLimit):
            KassalappClient("fake", opener=opener).search_products("chicken")
        self.assertEqual(count[0], 1)

    def test_invalid_search_rejected_before_api_call(self):
        api = KassalappClient("fake", opener=lambda *_args, **_kwargs: self.fail("called"))
        for name, size in [("ab", 20), ("valid", 0), ("valid", 101)]:
            with self.subTest(name=name, size=size):
                with self.assertRaises(ValueError):
                    api.search_products(name, size=size)

    def test_no_missing_credentials_allowed(self):
        with self.assertRaises(ValueError):
            KassalappClient("")

    def test_normalization_keeps_complete_as_review_only(self):
        staged = stage_product(synthetic_product())
        self.assertEqual(staged.review_status, "needs_review")
        self.assertEqual(staged.weight_g, "400")
        self.assertEqual(staged.protein_g_unverified_basis, "18")
        self.assertEqual(staged.price_nok, "60")

    def test_unknown_mass_units_and_store_are_not_ranked(self):
        staged = stage_product(synthetic_product(weight=2, weight_unit="piece", store=[]))
        self.assertEqual(staged.review_status, "incomplete")
        self.assertIn("weight_in_g_or_kg", staged.review_notes)
        self.assertIn("store", staged.review_notes)

    def test_kg_converted_to_g_without_basis_assumption(self):
        staged = stage_product(synthetic_product(weight=0.4, weight_unit="kg"))
        self.assertEqual(staged.weight_g, "400.0")
        self.assertEqual(staged.review_status, "needs_review")

    def test_missing_protein_is_incomplete(self):
        staged = stage_product(synthetic_product(nutrition=[]))
        self.assertEqual(staged.review_status, "incomplete")
        self.assertIsNone(staged.protein_g_unverified_basis)

    def test_collection_stages_only_without_promoting_verified(self):
        class FakeClient:
            def search_products(self, *args, **kwargs):
                return {"data": [synthetic_product()]}
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "test.sqlite3"
            with patch("scripts.collect_kassalapp.KassalappClient.from_environment",
                       return_value=FakeClient()):
                result = collect("synthetic", dest, 1)
            self.assertEqual(result, (1, 1))
            with sqlite3.connect(dest) as db:
                statuses = db.execute(
                    "SELECT review_status, price_checked_at FROM kassalapp_staging"
                ).fetchall()
                self.assertEqual(statuses, [("needs_review", None)])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM nutrition_profiles").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
