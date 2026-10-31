"""TASK-033: synthetic input boundary for no-network secondary evidence audit."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.audit_tjek_secondary_evidence import (
    _candidate_key, _optional_views, _private_file,
)


class SecondaryEvidenceAuditTests(unittest.TestCase):
    def test_same_tjek_catalog_key_normalizes_name_but_not_price(self):
        tjek = {
            "dealer_id": "2686gD", "catalog_id": "test-week",
            "heading": "COOP   TORTELLONI",
            "pricing": {"price": 35.9},
        }
        public = {
            "businessPublicId": "2686gD",
            "publicationPublicId": "test-week",
            "name": "Coop Tortelloni", "price": 35.9
        }
        self.assertEqual(_candidate_key(tjek, public=False),
                         _candidate_key(public, public=True))
        self.assertNotEqual(_candidate_key(tjek, public=False),
                            _candidate_key(public | {"price": 29.9},
                                           public=True))

    def test_optional_views_empty_when_not_configured(self):
        with TemporaryDirectory() as root:
            self.assertEqual(_optional_views(None, Path(root)), [])

    def test_private_view_fixture_must_be_bounded_and_explicit_schema(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "saved.json"
            rows = {"record_type": "etilbudsavis-offers-v1",
                    "offers": [{"name": "Synthetic Example",
                                "price": 10}]}
            path.write_text(json.dumps(rows), encoding="utf-8")
            self.assertEqual(_optional_views(path, Path(root)), rows["offers"])
            path.write_text(json.dumps({"offers": rows["offers"]}),
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                _optional_views(path, Path(root))

    def test_private_input_must_not_follow_symlinks_or_escape_parent(self):
        with TemporaryDirectory() as root:
            parent = Path(root) / "private"
            parent.mkdir()
            external = Path(root) / "outside.json"
            external.write_text("{}", encoding="utf-8")
            link = parent / "linked.json"
            link.symlink_to(external)
            with self.assertRaises(ValueError):
                _private_file(link, parent, 1000)
            with self.assertRaises(ValueError):
                _private_file(external, parent, 1000)

    def test_no_implicit_gtin_match_in_public_offer_key(self):
        tjek = {
            "dealer_id": "2686gD", "catalog_id": "test-week",
            "heading": "Synthetic product",
            "pricing": {"price": 35.9},
        }
        self.assertEqual(
            _candidate_key(tjek, public=False),
            ("2686gD", "test-week", "synthetic product", 3590))


if __name__ == "__main__":
    unittest.main()
