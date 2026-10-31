"""TASK-077: read-only Mealie recipe snapshot (synthetic fixtures, no network)."""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from nutrivalue.mealie_read import (
    MealieError, MealieReader, api_token, import_snapshot, load_snapshot,
    normalize_ingredient, report, save_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "analytics" / "recipe_read_model.sql"
TOKEN = "synthetic-token-123"
BASE = "https://mealie.example.test"


def unit(name, abbreviation="", std_qty=None, std_unit=None):
    return {"name": name, "abbreviation": abbreviation, "standardQuantity": std_qty,
            "standardUnit": std_unit}


def ing(quantity, unit_=None, food=None, note="", display="", original=None):
    return {"quantity": quantity, "unit": unit_, "food": {"name": food} if food else None,
            "note": note, "display": display, "originalText": original,
            "referenceId": "00000000-0000-0000-0000-000000000001"}


RECIPES = {
    "synthetic-chicken-rice": {
        "id": "11111111-1111-1111-1111-111111111111", "slug": "synthetic-chicken-rice",
        "name": "Synthetic chicken rice", "recipeServings": 4, "recipeYieldQuantity": 0,
        "recipeYield": "", "dateUpdated": "2026-10-26T10:00:00", "updatedAt": "2026-10-26T10:00:00",
        "recipeIngredient": [ing(800, unit("gram", "g"), "kyllingfilet"),
                             ing(0.3, unit("kilogram", "kg"), "ris"),
                             ing(2, unit("dl"), "melk"),
                             ing(1, None, "løk"),
                             ing(0, None, None, note="salt og pepper", display="salt og pepper")]},
    "synthetic-oats": {
        "id": "22222222-2222-2222-2222-222222222222", "slug": "synthetic-oats",
        "name": "Synthetic oats", "recipeServings": 0,
        "recipeIngredient": [ing(70, unit("gram", "g"), "havregryn")]},
}


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str, status: int = 200):
        super().__init__(body)
        self.status, self._url = status, url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeMealie:
    """Serves list pages of one recipe each, plus details; records every request."""

    def __init__(self, recipes=RECIPES, redirect_host=None):
        self.recipes, self.requests, self.redirect_host = recipes, [], redirect_host

    def __call__(self, request, timeout):
        self.requests.append(request)
        assert request.get_method() == "GET"
        url = request.full_url
        if self.redirect_host:
            return FakeResponse(b"{}", f"https://{self.redirect_host}/api/recipes")
        path = url[len(BASE):]
        if path.startswith("/api/app/about"):
            body = {"version": "v3.28.0"}
        elif path.startswith("/api/recipes?"):
            page = int(dict(p.split("=") for p in path.split("?", 1)[1].split("&"))["page"])
            slugs = sorted(self.recipes)
            body = {"page": page, "perPage": 1, "total": len(slugs), "totalPages": len(slugs),
                    "items": [{"slug": slugs[page - 1]}]}
        else:
            body = self.recipes[path.rsplit("/", 1)[1]]
        return FakeResponse(json.dumps(body).encode(), url)


def reader(fake=None):
    return MealieReader(BASE, TOKEN, opener=fake or FakeMealie(), sleep=lambda s: None)


class NormalizeTests(unittest.TestCase):
    def test_mass_units_become_grams(self):
        self.assertEqual(normalize_ingredient(ing(800, unit("gram", "g"), "x"))["grams"], 800)
        self.assertEqual(normalize_ingredient(ing(0.3, unit("kilogram", "kg"), "x"))["grams"], 300)
        custom = unit("pakke", "pk", std_qty=400, std_unit="gram")   # unit with a standard
        self.assertEqual(normalize_ingredient(ing(2, custom, "x"))["grams"], 800)

    def test_volume_count_and_text_are_gaps(self):
        volume = normalize_ingredient(ing(2, unit("dl"), "melk"))
        self.assertEqual((volume["grams"], volume["millilitres"], volume["amount_status"]),
                         (None, 200, "volume"))
        self.assertEqual(normalize_ingredient(ing(1, None, "løk"))["amount_status"], "count")
        self.assertEqual(normalize_ingredient(ing(1, unit("fedd"), "hvitløk"))["amount_status"],
                         "other_unit")
        self.assertEqual(normalize_ingredient(ing(0, unit("gram", "g"), "ris"))["amount_status"],
                         "no_quantity")
        self.assertEqual(normalize_ingredient(ing(0, None, None, note="salt"))["amount_status"],
                         "no_food")


class ReaderTests(unittest.TestCase):
    def test_only_get_requests_with_bearer_token(self):
        fake = FakeMealie()
        with tempfile.TemporaryDirectory() as tmp:
            folder = save_snapshot(reader(fake), Path(tmp),
                                   now=datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
            manifest = json.loads((folder / "manifest.json").read_text())
        self.assertTrue(all(r.get_method() == "GET" for r in fake.requests))
        self.assertTrue(all(r.get_header("Authorization") == f"Bearer {TOKEN}"
                            for r in fake.requests))
        self.assertEqual(manifest["recipe_count"], 2)        # followed both list pages
        self.assertEqual(manifest["mealie_version"], "v3.28.0")
        self.assertNotIn(TOKEN, json.dumps(manifest))
        self.assertFalse(any(hasattr(MealieReader, n) for n in ("post", "put", "patch", "delete")))

    def test_base_url_and_paths_are_restricted(self):
        for bad in ("http://mealie.example.test", "https://mealie.example.test/x",
                    "https://" + "user" + ":" + "pw" + "@mealie.example.test", "https://mealie.example.test?a=1"):
            with self.assertRaises(MealieError):
                MealieReader(bad, TOKEN)
        with self.assertRaises(MealieError):
            MealieReader(BASE, "")
        with self.assertRaises(MealieError):
            reader().get("/admin/users")

    def test_other_host_and_http_errors_fail_without_token(self):
        with self.assertRaises(MealieError):
            reader(FakeMealie(redirect_host="evil.example.test")).recipe_slugs()

        def refused(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(TOKEN.encode()))
        with self.assertRaises(MealieError) as caught:
            MealieReader(BASE, TOKEN, opener=refused).recipe_slugs()
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIn("401", str(caught.exception))

    def test_token_from_environment_or_keychain(self):
        self.assertEqual(api_token({"MEALIE_API_TOKEN": " t "}, "linux"), "t")

        class Done:
            returncode, stdout = 0, "from-keychain\n"
        calls = []
        got = api_token({}, "darwin", run=lambda cmd, **kw: calls.append(cmd) or Done())
        self.assertEqual(got, "from-keychain")
        self.assertIn("nutrivalue-mealie", calls[0])
        self.assertEqual(api_token({}, "linux"), "")


class ImportTests(unittest.TestCase):
    def _snapshot(self, tmp, recipes=RECIPES, hour=12):
        folder = save_snapshot(reader(FakeMealie(recipes)), Path(tmp),
                               now=datetime(2026, 10, 26, hour, tzinfo=timezone.utc))
        return folder, load_snapshot(folder)

    def test_import_is_append_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, snap = self._snapshot(tmp)
            db = sqlite3.connect(":memory:")
            self.assertTrue(import_snapshot(db, snap, SCHEMA))
            self.assertFalse(import_snapshot(db, snap, SCHEMA))
            grams = db.execute("SELECT food_name, grams FROM mealie_recipe_ingredients "
                               "WHERE amount_status = 'grams' ORDER BY food_name").fetchall()
            self.assertEqual(grams, [("havregryn", 70.0), ("kyllingfilet", 800.0), ("ris", 300.0)])
            gaps = db.execute("SELECT amount_status FROM v_mealie_ingredient_gaps "
                              "ORDER BY position").fetchall()
            self.assertEqual(sorted(g[0] for g in gaps), ["count", "no_food", "volume"])
            servings = dict(db.execute("SELECT slug, servings FROM mealie_recipes"))
            self.assertEqual(servings, {"synthetic-chicken-rice": 4.0, "synthetic-oats": None})

            changed = json.loads(json.dumps(RECIPES))
            changed["synthetic-oats"]["recipeIngredient"][0]["quantity"] = 80
            _, snap2 = self._snapshot(tmp, changed, hour=13)
            self.assertTrue(import_snapshot(db, snap2, SCHEMA))
            hashes = db.execute("SELECT COUNT(DISTINCT content_sha256) FROM mealie_recipes "
                                "WHERE slug = 'synthetic-oats'").fetchone()[0]
            self.assertEqual(hashes, 2)                     # old revision kept, new one added
            current = db.execute("SELECT i.grams FROM v_mealie_current_recipes r JOIN "
                                 "mealie_recipe_ingredients i USING (snapshot_id, recipe_id) "
                                 "WHERE r.slug = 'synthetic-oats'").fetchone()[0]
            self.assertEqual(current, 80.0)

    def test_tampered_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, _ = self._snapshot(tmp)
            (folder / "recipes" / "synthetic-oats.json").write_text("{}")
            with self.assertRaises(MealieError):
                load_snapshot(folder)

    def test_report_lists_gaps_and_missing_servings(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, snap = self._snapshot(tmp)
        text = "\n".join(report(snap))
        self.assertIn("salt og pepper (no_food)", text)
        self.assertIn("Synthetic oats: number of servings is not set", text)


if __name__ == "__main__":
    unittest.main()
