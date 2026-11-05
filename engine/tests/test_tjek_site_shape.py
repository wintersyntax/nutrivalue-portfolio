"""TASK-033: synthetic offline structural diagnostics on saved HTML."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from nutrivalue.providers.tjek_site_shape import diagnose_site_html_shape
from scripts.inspect_etilbudsavis_html_shape import inspect_private_html


TARGET = "W8l7eqAB"


def literal_html():
    fixture = {
        "data": [{
            "publicId": "test-sample",
            "publicationPublicId": TARGET,
            "businessPublicId": "synthetic-dealer",
            "membershipPrice": 22,
            "pieceCountGet": 3,
            "pieceCountGetFor": 2,
            "name": "Synthetic protein 999 NOK",
            "ean": "7039010019828",
        }]
    }
    return ("<html><script>window.data=" +
            json.dumps(fixture) + ";</script></html>").encode()


def flight_html():
    inner = json.dumps({
        "data": [{
            "publicId": "test-sample",
            "publicationPublicId": TARGET,
            "businessPublicId": "synthetic-dealer",
            "name": "Synthetic protein 999 NOK",
        }]
    })
    return ("<html><script>self.__next_f.push(" +
            json.dumps([1, inner]) + ")</script></html>").encode()


class PrivateSiteShapeTests(unittest.TestCase):
    def test_literal_json_is_counted_with_optional_fields(self):
        result = diagnose_site_html_shape(literal_html(), target_catalog=TARGET)
        self.assertEqual(result["literal_data_array_starts"], 1)
        self.assertEqual(result["next_f_push_calls"], 0)
        self.assertEqual(result["raw_marker:membershipPrice"], 1)
        self.assertEqual(result["catalog_occurrences_in_html"], 1)
        self.assertFalse(result["rank_eligible"])

    def test_next_f_flight_string_is_detected_once_not_assumed_offers(self):
        result = diagnose_site_html_shape(flight_html(), target_catalog=TARGET)
        self.assertEqual(result["next_f_push_calls"], 1)
        self.assertEqual(result["next_f_decoded_arrays"], 1)
        self.assertEqual(result["next_f_string_segments"], 1)
        self.assertEqual(result["decoded_flight_data_array_starts"], 1)
        self.assertEqual(result["decoded_flight_marker:publicId"], 1)
        self.assertEqual(result["catalog_occurrences_in_decoded_flight"], 1)
        self.assertEqual(result["literal_data_array_starts"], 0)

    def test_real_offer_content_not_in_output(self):
        for html in (literal_html(), flight_html()):
            result = diagnose_site_html_shape(html, target_catalog=TARGET)
            for secret in ("Synthetic protein", "999 NOK",
                           "test-sample", "7039010019828"):
                self.assertNotIn(secret, str(result))

    def test_unrecognized_or_empty_page_still_reports_zero_evidence(self):
        html = b"<html><script>const x=1;</script></html>"
        result = diagnose_site_html_shape(html, target_catalog=TARGET)
        self.assertEqual(result["next_f_push_calls"], 0)
        self.assertEqual(result["literal_data_array_starts"], 0)
        self.assertEqual(result["raw_marker:publicId"], 0)
        self.assertEqual(result["network_requests"], 0)

    def test_bad_flight_segment_is_only_a_count_not_content(self):
        html = b"<script>self.__next_f.push(unparsed)</script>"
        result = diagnose_site_html_shape(html, target_catalog=TARGET)
        self.assertEqual(result["next_f_push_calls"], 1)
        self.assertEqual(result["next_f_unrecognized_arrays"], 1)

    def test_private_sha_gates_offline_processing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            pathdir = root / "api-view-compare"
            pathdir.mkdir()
            path = pathdir / "etilbudsavis-husholdning-20261016T150436984303Z.html"
            body = flight_html()
            path.write_bytes(body)
            sha = hashlib.sha256(body).hexdigest()
            result = inspect_private_html(path, expected_sha256=sha,
                                          private_root=root, catalog_id=TARGET)
            self.assertEqual(result["next_f_decoded_arrays"], 1)
            with self.assertRaises(ValueError):
                inspect_private_html(path, expected_sha256="0" * 64,
                                     private_root=root, catalog_id=TARGET)

    def test_private_root_rejects_symlink_and_traversal(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            pathdir = root / "api-view-compare"
            pathdir.mkdir()
            outside = root / "outside.html"
            outside.write_bytes(literal_html())
            link = pathdir / "etilbudsavis-husholdning-20261016T150436984303Z.html"
            link.symlink_to(outside)
            sha = hashlib.sha256(literal_html()).hexdigest()
            for path in (link, outside):
                with self.assertRaises(ValueError):
                    inspect_private_html(path, expected_sha256=sha,
                                         private_root=root, catalog_id=TARGET)

    def test_inspector_direct_cli_import_with_no_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        run = subprocess.run(
            [sys.executable,
             str(root / "scripts/inspect_etilbudsavis_html_shape.py"),
             "--help"],
            cwd=root, env=env, capture_output=True, text=True,
            timeout=10, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--expect-sha256", run.stdout)


if __name__ == "__main__":
    unittest.main()
