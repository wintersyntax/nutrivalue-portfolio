"""TASK-077: read-only Mealie recipe snapshot for the private NutriValue read model.

NutriValue only READS the owner's Mealie recipes (GET requests, bearer API
token); it never creates, changes or deletes anything in Mealie. Each run saves
the raw recipe JSON privately with SHA-256 hashes and imports an append-only
snapshot into SQLite (`analytics/recipe_read_model.sql`). Ingredient amounts are
converted to grams only when the unit is a mass unit; volumes, pieces and
unparsed text are kept as they are and listed as gaps for the owner.

The token comes from MEALIE_API_TOKEN or, on macOS, the login Keychain service
`nutrivalue-mealie`. It is never printed, logged, stored or put in an error.
Field names follow the Mealie v3 API (camelCase): `recipeIngredient` items with
`quantity`, `unit` (`name`, `abbreviation`, `standardQuantity`,
`standardUnit`), `food` (`name`), `note`, `display`, `originalText`, `title`,
`referenceId`; recipes with `id`, `slug`, `name`, `recipeServings`,
`recipeYieldQuantity`, `recipeYield`, `dateUpdated`, `updatedAt`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE_URL = ""  # Explicit caller configuration; no bundled instance address.
KEYCHAIN_SERVICE = "nutrivalue-mealie"
PER_PAGE = 50
MAX_PAGES = 20            # at most 1,000 recipes per run
MAX_RESPONSE = 5_000_000  # bytes per response
TIMEOUT_S = 20

MASS_UNITS = {  # unit name/abbreviation (lower case) -> grams per unit
    "g": 1.0, "gr": 1.0, "gram": 1.0, "grams": 1.0, "gramm": 1.0, "grammes": 1.0,
    "kg": 1000.0, "kilo": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0,
    "mg": 0.001, "milligram": 0.001, "milligrams": 0.001,
    "hg": 100.0, "hektogram": 100.0,
    "oz": 28.349523125, "ounce": 28.349523125, "ounces": 28.349523125,
    "lb": 453.59237, "lbs": 453.59237, "pound": 453.59237, "pounds": 453.59237,
}
STANDARD_MASS = {"gram": 1.0, "kilogram": 1000.0, "ounce": 28.349523125, "pound": 453.59237}
VOLUME_UNITS = {  # -> millilitres per unit; grams need a density, so never converted
    "ml": 1.0, "milliliter": 1.0, "millilitre": 1.0, "milliliters": 1.0,
    "cl": 10.0, "dl": 100.0, "l": 1000.0, "liter": 1000.0, "litre": 1000.0, "liters": 1000.0,
}
STANDARD_VOLUME = {"milliliter": 1.0, "liter": 1000.0, "cup": 236.5882365,
                   "fluid_ounce": 29.5735295625}


class MealieError(RuntimeError):
    """A read failed; the message never contains the token or a response body."""


def api_token(environ=None, platform: str | None = None, run=subprocess.run) -> str:
    """MEALIE_API_TOKEN from the environment, else (macOS) from the login Keychain."""
    environ = os.environ if environ is None else environ
    token = (environ.get("MEALIE_API_TOKEN") or "").strip()
    if token:
        return token
    if (platform or sys.platform) == "darwin":
        try:
            done = run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                       capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return ""
        if done.returncode == 0:
            return done.stdout.strip()
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never follow a redirect
        return None


def _default_opener():
    return urllib.request.build_opener(_NoRedirect()).open


class MealieReader:
    """GET-only client. There is deliberately no method that writes."""

    def __init__(self, base_url: str, token: str, opener=None, interval_s: float = 0.2,
                 sleep=time.sleep):
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") \
                or parts.query or parts.fragment or parts.username or parts.password:
            raise MealieError("base URL must be https://<host> without path, query or login")
        if not token:
            raise MealieError("no Mealie API token (MEALIE_API_TOKEN or Keychain "
                              f"service {KEYCHAIN_SERVICE})")
        self.base = f"https://{parts.netloc}"
        self.host = parts.hostname
        self._token = token
        self._open = opener or _default_opener()
        self._interval = interval_s
        self._sleep = sleep
        self.calls = 0

    def get(self, path: str, params: dict | None = None) -> dict:
        if not path.startswith("/api/") or ".." in path:
            raise MealieError("only /api/ paths are read")
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        request = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/json",
            "User-Agent": "NutriValue-read-only/1"})
        if self.calls:
            self._sleep(self._interval)
        self.calls += 1
        try:
            with self._open(request, timeout=TIMEOUT_S) as response:
                status = getattr(response, "status", 200)
                final = urllib.parse.urlsplit(response.geturl()).hostname \
                    if hasattr(response, "geturl") else self.host
                if final != self.host:
                    raise MealieError("response came from another host")
                if status != 200:
                    raise MealieError(f"HTTP {status} for {path}")
                body = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            raise MealieError(f"HTTP {exc.code} for {path}") from None
        except urllib.error.URLError as exc:
            raise MealieError(f"cannot reach Mealie: {type(exc.reason).__name__}") from None
        if len(body) > MAX_RESPONSE:
            raise MealieError(f"response for {path} is larger than {MAX_RESPONSE} bytes")
        try:
            data = json.loads(body)
        except ValueError:
            raise MealieError(f"response for {path} is not JSON") from None
        if not isinstance(data, dict):
            raise MealieError(f"response for {path} is not a JSON object")
        return data

    def recipe_slugs(self) -> list[str]:
        slugs: list[str] = []
        for page in range(1, MAX_PAGES + 1):
            data = self.get("/api/recipes", {"page": page, "perPage": PER_PAGE,
                                             "orderBy": "name", "orderDirection": "asc"})
            items = data.get("items")
            if not isinstance(items, list):
                raise MealieError("recipe list has no items")
            for item in items:
                slug = item.get("slug") if isinstance(item, dict) else None
                if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{1,200}", slug):
                    raise MealieError("recipe list contains an unusable slug")
                slugs.append(slug)
            total_pages = data.get("totalPages") or 0
            if page >= total_pages or not items:
                return slugs
        raise MealieError(f"more than {MAX_PAGES * PER_PAGE} recipes; raise MAX_PAGES on purpose")

    def recipe(self, slug: str) -> dict:
        return self.get("/api/recipes/" + urllib.parse.quote(slug, safe=""))

    def version(self) -> str | None:
        try:
            about = self.get("/api/app/about")
        except MealieError:
            return None
        version = about.get("version")
        return version if isinstance(version, str) else None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def save_snapshot(reader: MealieReader, out_root: Path, now: datetime | None = None) -> Path:
    """Read every recipe and save the raw JSON plus a manifest. Returns the folder."""
    now = now or datetime.now(timezone.utc)
    folder = out_root / now.strftime("%Y%m%dT%H%M%SZ")
    (folder / "recipes").mkdir(parents=True, exist_ok=False)
    slugs = reader.recipe_slugs()
    files = []
    for slug in slugs:
        body = _canonical(reader.recipe(slug))
        name = f"recipes/{slug}.json"
        (folder / name).write_bytes(body)
        files.append({"slug": slug, "file": name, "sha256": _sha(body)})
    manifest = {"schema_version": 1, "source": "mealie", "host": reader.host,
                "mealie_version": reader.version(), "fetched_at": now.isoformat(),
                "recipe_count": len(files), "calls": reader.calls, "recipes": files}
    (folder / "manifest.json").write_bytes(_canonical(manifest))
    return folder


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize_ingredient(item: dict) -> dict:
    """One Mealie ingredient -> amount in grams when the unit says mass, else a gap."""
    quantity = _number(item.get("quantity"))
    unit = item.get("unit") if isinstance(item.get("unit"), dict) else None
    food = item.get("food") if isinstance(item.get("food"), dict) else None
    unit_name = _text(unit.get("name")) if unit else None
    unit_abbr = _text(unit.get("abbreviation")) if unit else None
    std_qty = _number(unit.get("standardQuantity")) if unit else None
    std_unit = _text(unit.get("standardUnit")) if unit else None
    food_name = _text(food.get("name")) if food else None
    row = {"title": _text(item.get("title")), "quantity": quantity, "unit_name": unit_name,
           "unit_abbreviation": unit_abbr, "unit_standard_quantity": std_qty,
           "unit_standard_unit": std_unit, "food_name": food_name,
           "note": _text(item.get("note")), "display": _text(item.get("display")),
           "original_text": _text(item.get("originalText")),
           "reference_id": _text(item.get("referenceId")),
           "grams": None, "millilitres": None, "amount_status": None}
    if food_name is None:
        row["amount_status"] = "no_food"          # a heading or text the parser did not split
        return row
    if not quantity or quantity < 0:
        row["amount_status"] = "no_quantity"
        return row
    names = {n.lower() for n in (unit_name, unit_abbr) if n}
    per_gram = next((MASS_UNITS[n] for n in names if n in MASS_UNITS), None)
    if per_gram is None and std_qty and std_unit in STANDARD_MASS:
        per_gram = std_qty * STANDARD_MASS[std_unit]
    if per_gram is not None:
        row["grams"] = round(quantity * per_gram, 3)
        row["amount_status"] = "grams"
        return row
    per_ml = next((VOLUME_UNITS[n] for n in names if n in VOLUME_UNITS), None)
    if per_ml is None and std_qty and std_unit in STANDARD_VOLUME:
        per_ml = std_qty * STANDARD_VOLUME[std_unit]
    if per_ml is not None:
        row["millilitres"] = round(quantity * per_ml, 3)
        row["amount_status"] = "volume"           # needs a density before it has grams
        return row
    row["amount_status"] = "count" if unit is None else "other_unit"
    return row


def _recipe_row(recipe: dict) -> dict:
    recipe_id, slug = _text(recipe.get("id")), _text(recipe.get("slug"))
    if not recipe_id or not slug:
        raise MealieError("a recipe has no id or slug")
    ingredients = recipe.get("recipeIngredient")
    if not isinstance(ingredients, list):
        raise MealieError(f"recipe {slug} has no ingredient list")
    return {"recipe_id": recipe_id, "slug": slug, "name": _text(recipe.get("name")) or slug,
            "servings": _number(recipe.get("recipeServings")) or None,
            "yield_quantity": _number(recipe.get("recipeYieldQuantity")) or None,
            "yield_text": _text(recipe.get("recipeYield")),
            "date_updated": _text(recipe.get("dateUpdated")),
            "updated_at": _text(recipe.get("updatedAt")),
            "ingredients": [normalize_ingredient(i if isinstance(i, dict) else {})
                            for i in ingredients]}


def load_snapshot(folder: Path) -> dict:
    """Verify hashes and normalize a saved snapshot (no network)."""
    manifest_bytes = (folder / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema_version") != 1 or manifest.get("source") != "mealie":
        raise MealieError("not a Mealie snapshot manifest")
    recipes = []
    for entry in manifest["recipes"]:
        path = (folder / entry["file"]).resolve()
        if folder.resolve() not in path.parents:
            raise MealieError("manifest points outside the snapshot")
        body = path.read_bytes()
        if _sha(body) != entry["sha256"]:
            raise MealieError(f"hash mismatch for {entry['file']}")
        row = _recipe_row(json.loads(body))
        row["content_sha256"] = entry["sha256"]
        recipes.append(row)
    if len(recipes) != manifest["recipe_count"]:
        raise MealieError("recipe count does not match the manifest")
    return {"snapshot_id": _sha(manifest_bytes), "fetched_at": manifest["fetched_at"],
            "host": manifest["host"], "mealie_version": manifest.get("mealie_version"),
            "recipes": recipes}


INGREDIENT_COLUMNS = ("title", "quantity", "unit_name", "unit_abbreviation",
                      "unit_standard_quantity", "unit_standard_unit", "food_name", "note",
                      "display", "original_text", "reference_id", "grams", "millilitres",
                      "amount_status")


def import_snapshot(db: sqlite3.Connection, snapshot: dict, schema_path: Path) -> bool:
    """Append-only import; the same snapshot again is a no-op. Returns True if new."""
    db.executescript(schema_path.read_text(encoding="utf-8"))
    if db.execute("SELECT 1 FROM mealie_snapshots WHERE snapshot_id = ?",
                  (snapshot["snapshot_id"],)).fetchone():
        return False
    with db:
        db.execute("INSERT INTO mealie_snapshots (snapshot_id, fetched_at, host, mealie_version,"
                   " recipe_count) VALUES (?, ?, ?, ?, ?)",
                   (snapshot["snapshot_id"], snapshot["fetched_at"], snapshot["host"],
                    snapshot["mealie_version"], len(snapshot["recipes"])))
        for r in snapshot["recipes"]:
            db.execute("INSERT INTO mealie_recipes (snapshot_id, recipe_id, slug, name, servings,"
                       " yield_quantity, yield_text, date_updated, updated_at, content_sha256)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (snapshot["snapshot_id"], r["recipe_id"], r["slug"], r["name"],
                        r["servings"], r["yield_quantity"], r["yield_text"], r["date_updated"],
                        r["updated_at"], r["content_sha256"]))
            for position, ing in enumerate(r["ingredients"], 1):
                db.execute(
                    "INSERT INTO mealie_recipe_ingredients (snapshot_id, recipe_id, position, "
                    + ", ".join(INGREDIENT_COLUMNS) + ") VALUES (?, ?, ?, "
                    + ", ".join("?" * len(INGREDIENT_COLUMNS)) + ")",
                    (snapshot["snapshot_id"], r["recipe_id"], position,
                     *(ing[c] for c in INGREDIENT_COLUMNS)))
    return True


def report(snapshot: dict) -> list[str]:
    lines = [f"# Mealie recipes ({snapshot['fetched_at']}, Mealie "
             f"{snapshot['mealie_version'] or 'version unknown'})", "",
             f"- Recipes: {len(snapshot['recipes'])}", "",
             "| recipe | servings | ingredients | in grams | volume | count/other | no amount |",
             "|---|---|---|---|---|---|---|"]
    gaps = []
    for r in snapshot["recipes"]:
        status = [i["amount_status"] for i in r["ingredients"]]
        food = [s for s in status if s != "no_food"]
        lines.append(f"| {r['name']} | {r['servings'] or '—'} | {len(food)} | "
                     f"{status.count('grams')} | {status.count('volume')} | "
                     f"{status.count('count') + status.count('other_unit')} | "
                     f"{status.count('no_quantity') + status.count('no_food')} |")
        for i in r["ingredients"]:
            if i["amount_status"] != "grams":
                text = i["display"] or i["original_text"] or i["food_name"] or i["note"] or ""
                gaps.append(f"- {r['name']}: {text} ({i['amount_status']})")
        if not r["servings"]:
            gaps.append(f"- {r['name']}: number of servings is not set")
    lines += ["", "## Needs grams or servings", ""] + (gaps or ["- none"])
    return lines
