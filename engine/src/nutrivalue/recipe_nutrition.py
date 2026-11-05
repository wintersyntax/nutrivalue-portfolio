"""TASK-080: kcal and macros per serving for the owner's Mealie recipes.

Each recipe ingredient (Mealie food name) gets an owner-reviewed nutrition
source: a Matvaretabellen food (``mvt``, a generic form-matched estimate) or a
package label the owner copied (``owner_label``, owner-reported). Labels may be
per 100 g or per 100 ml; ml is turned into grams only with a density the owner
gave. Nothing is guessed: an ingredient without grams or without a source is
reported, and the recipe total is then marked incomplete instead of looking exact.

    batch = sum(grams_i * value_i_per_100g / 100); per serving = batch / servings
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

NUTRIENTS = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")
MVT_NUTRIENT = {"protein_g": "Protein", "fat_g": "Fett", "carbs_g": "Karbo", "fiber_g": "Fiber"}
DENSITY_RANGE = (0.3, 2.5)


class RecipeNutritionError(ValueError):
    """A review input that cannot be stored or used."""


def ingredient_key(name: str) -> str:
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not key:
        raise RecipeNutritionError("ingredient name is empty")
    return key


def _density(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not DENSITY_RANGE[0] <= value <= DENSITY_RANGE[1]:
        raise RecipeNutritionError("density must be between 0.3 and 2.5 g/ml")
    return value


def _latest_mvt_snapshot(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT id FROM mvt_snapshots WHERE locale='nb' "
                     "ORDER BY retrieved_at DESC, id DESC LIMIT 1").fetchone()
    if row is None:
        raise RecipeNutritionError("no Matvaretabellen snapshot imported")
    return row[0]


def add_label(db: sqlite3.Connection, name: str, *, basis: str, kcal: float, protein_g: float,
              fat_g=None, carbs_g=None, fiber_g=None, density_g_per_ml=None, note: str,
              entered_at: str) -> None:
    if basis not in ("per_100g", "per_100ml"):
        raise RecipeNutritionError("basis must be per_100g or per_100ml")
    if basis == "per_100ml" and density_g_per_ml is None:
        raise RecipeNutritionError("a per_100ml label needs --label-density (g per ml)")
    if not (note or "").strip():
        raise RecipeNutritionError("a label needs a note (e.g. product and where the values come from)")
    with db:
        db.execute("INSERT INTO owner_product_labels (label_name, entered_at, basis, "
                   "density_g_per_ml, kcal, protein_g, fat_g, carbs_g, fiber_g, note) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (ingredient_key(name), entered_at, basis, _density(density_g_per_ml),
                    kcal, protein_g, fat_g, carbs_g, fiber_g, note.strip()))


def map_ingredient(db: sqlite3.Connection, ingredient: str, source: str, *, note: str,
                   reviewed_at: str, density_g_per_ml=None) -> str:
    """``source`` is ``mvt:<food id>`` or ``label:<label name>``. Returns a description."""
    name = ingredient_key(ingredient)
    if not (note or "").strip():
        raise RecipeNutritionError("a mapping needs a note")
    kind, _, ref = source.partition(":")
    ref = ref.strip()
    if kind == "mvt":
        snapshot = _latest_mvt_snapshot(db)
        food = db.execute("SELECT food_name FROM mvt_foods WHERE snapshot_id=? AND food_id=?",
                          (snapshot, ref)).fetchone()
        if food is None:
            raise RecipeNutritionError(f"{ref!r} is not in Matvaretabellen snapshot {snapshot}")
        row = ("mvt", snapshot, ref, None)
        described = f"{name} -> Matvaretabellen {ref} {food[0]}"
    elif kind == "label":
        label = ingredient_key(ref)
        if db.execute("SELECT 1 FROM owner_product_labels WHERE label_name=?",
                      (label,)).fetchone() is None:
            raise RecipeNutritionError(f"no label named {label!r}; add it with --label first")
        row = ("owner_label", None, None, label)
        described = f"{name} -> your label {label!r}"
    else:
        raise RecipeNutritionError("source must be mvt:<food id> or label:<label name>")
    with db:
        db.execute("INSERT INTO recipe_ingredient_sources (ingredient_name, reviewed_at, "
                   "source_kind, mvt_snapshot_id, mvt_food_id, label_name, density_g_per_ml, "
                   "note) VALUES (?,?,?,?,?,?,?,?)",
                   (name, reviewed_at, *row, _density(density_g_per_ml), note.strip()))
    return described


def _per_100g(db: sqlite3.Connection, source: dict) -> tuple[dict, str, float | None]:
    """Nutrients per 100 g, evidence label and the source's own density (labels)."""
    if source["source_kind"] == "mvt":
        snap, food = source["mvt_snapshot_id"], source["mvt_food_id"]
        kcal = db.execute("SELECT energy_kcal FROM mvt_foods WHERE snapshot_id=? AND food_id=?",
                          (snap, food)).fetchone()
        values = {"kcal": float(kcal[0]) if kcal and kcal[0] not in (None, "") else None}
        for key, nutrient in MVT_NUTRIENT.items():
            row = db.execute("SELECT quantity_per_100g FROM mvt_food_nutrients WHERE "
                             "snapshot_id=? AND food_id=? AND nutrient_id=? AND unit='g'",
                             (snap, food, nutrient)).fetchone()
            values[key] = float(row[0]) if row and row[0] not in (None, "") else None
        return values, "generic_estimate", None
    label = db.execute("SELECT * FROM v_owner_label_current WHERE label_name=?",
                       (source["label_name"],)).fetchone()
    values = {k: label[k] for k in NUTRIENTS}
    if label["basis"] == "per_100ml":   # per 100 ml -> per 100 g: divide by g/ml
        values = {k: (v / label["density_g_per_ml"] if v is not None else None)
                  for k, v in values.items()}
    return values, "owner_label", label["density_g_per_ml"]


def recipe_nutrition(db: sqlite3.Connection) -> list[dict]:
    """Per current Mealie recipe: ingredients with grams, source and nutrients."""
    db.row_factory = sqlite3.Row
    sources = {r["ingredient_name"]: dict(r) for r in
               db.execute("SELECT * FROM v_recipe_ingredient_source_current")}
    out = []
    for recipe in db.execute("SELECT * FROM v_mealie_current_recipes ORDER BY name").fetchall():
        rows = db.execute("SELECT * FROM mealie_recipe_ingredients WHERE snapshot_id=? AND "
                          "recipe_id=? AND food_name IS NOT NULL ORDER BY position",
                          (recipe["snapshot_id"], recipe["recipe_id"])).fetchall()
        lines, totals, gaps = [], {k: 0.0 for k in NUTRIENTS}, []
        for ing in rows:
            name = ingredient_key(ing["food_name"])
            line = {"ingredient": ing["food_name"], "text": ing["display"] or ing["food_name"],
                    "grams": None, "evidence": None, "source": None}
            source = sources.get(name)
            if source is None:
                line["evidence"] = "no_source"
            else:
                per100, evidence, label_density = _per_100g(db, source)
                line["evidence"] = evidence
                line["source"] = (f"Matvaretabellen {source['mvt_food_id']}"
                                  if source["source_kind"] == "mvt"
                                  else f"label {source['label_name']}")
                density = source["density_g_per_ml"] or label_density
                if ing["grams"] is not None:
                    line["grams"] = ing["grams"]
                elif ing["millilitres"] is not None and density:
                    line["grams"] = round(ing["millilitres"] * density, 3)
                    line["grams_from"] = f"{ing['millilitres']:g} ml x {density:g} g/ml"
                if line["grams"] is None:
                    line["evidence"] = ("no_density" if ing["millilitres"] is not None
                                        else "no_grams")
                else:
                    for key in NUTRIENTS:
                        value = per100[key]
                        contribution = None if value is None else line["grams"] * value / 100
                        line[key] = contribution
                        if contribution is None or totals[key] is None:
                            totals[key] = None
                        else:
                            totals[key] += contribution
            if line["evidence"] in ("no_source", "no_grams", "no_density"):
                gaps.append(f"{line['text']}: {line['evidence']}")
            lines.append(line)
        servings = recipe["servings"]
        complete = not gaps and bool(servings)
        per_serving = {k: (round(v / servings, 1) if v is not None and servings else None)
                       for k, v in totals.items()}
        if not servings:
            gaps.append("number of servings is not set in Mealie")
        out.append({"recipe": recipe["name"], "servings": servings, "lines": lines,
                    "batch": {k: (round(v, 1) if v is not None else None)
                              for k, v in totals.items()},
                    "per_serving": per_serving,
                    "status": "complete_estimate" if complete else "incomplete",
                    "evidence": sorted({l["evidence"] for l in lines}), "gaps": gaps})
    return out


def suggestions(db: sqlite3.Connection, approved_query: Path) -> dict[str, list[str]]:
    """Unmapped ingredient -> approved food_key references whose key matches its words."""
    db.row_factory = sqlite3.Row
    mapped = {r[0] for r in db.execute("SELECT ingredient_name FROM v_recipe_ingredient_source_current")}
    approved = list(db.execute(Path(approved_query).read_text(encoding="utf-8")))

    generic = {"cut", "cold", "frozen", "fresh", "raw", "whole", "dried", "dry", "plain",
               "natural", "organic", "large", "small"}

    def words(text):
        return {w.rstrip("s") for w in re.findall(r"[a-zæøå]+", text.lower())
                if len(w) > 2} - generic
    out = {}
    for (food,) in db.execute("SELECT DISTINCT lower(trim(food_name)) FROM mealie_recipe_ingredients i "
                              "JOIN v_mealie_current_recipes r USING (snapshot_id, recipe_id) "
                              "WHERE food_name IS NOT NULL ORDER BY 1"):
        if food in mapped:
            continue
        scored = [(len(words(a["food_key"].replace("_", " ")) & words(food)), a)
                  for a in approved]
        best = max((n for n, _ in scored), default=0)
        out[food] = [f"mvt:{a['reference_food_id']}  ({a['food_key']}: {a['reference_food_name']})"
                     for n, a in scored if best and n == best]
    return out


def search_mvt(db: sqlite3.Connection, text: str, limit: int = 15) -> list[tuple]:
    snapshot = _latest_mvt_snapshot(db)
    return db.execute("SELECT food_id, food_name, energy_kcal FROM mvt_foods WHERE snapshot_id=? "
                      "AND lower(food_name) LIKE ? ORDER BY food_name LIMIT ?",
                      (snapshot, f"%{text.lower()}%", limit)).fetchall()


def report(results: list[dict]) -> list[str]:
    lines = []
    for r in results:
        p = r["per_serving"]
        def show(v, unit=""):
            return "—" if v is None else f"{v:g}{unit}"
        lines += [f"## {r['recipe']} ({show(r['servings'])} servings) — {r['status']}", "",
                  ("Per serving" if r["status"] == "complete_estimate" else
                   "Per serving, INCOMPLETE (missing ingredients not counted)")
                  + f": {show(p['kcal'], ' kcal')}, protein {show(p['protein_g'], ' g')}, "
                  f"fat {show(p['fat_g'], ' g')}, carbs {show(p['carbs_g'], ' g')}, "
                  f"fiber {show(p['fiber_g'], ' g')}", "",
                  "| ingredient | grams | source | evidence | kcal | protein g |",
                  "|---|---|---|---|---|---|"]
        for l in r["lines"]:
            grams = show(l["grams"]) + (f" ({l['grams_from']})" if l.get("grams_from") else "")
            lines.append(f"| {l['text']} | {grams} | {l['source'] or '—'} | {l['evidence']} | "
                         f"{show(round(l['kcal'], 1) if l.get('kcal') is not None else None)} | "
                         f"{show(round(l['protein_g'], 1) if l.get('protein_g') is not None else None)} |")
        if r["gaps"]:
            lines += ["", "Missing: " + "; ".join(r["gaps"])]
        lines += ["", "generic_estimate = Matvaretabellen reference food, not your exact product; "
                  "owner_label = values you copied from the package.", ""]
    return lines
