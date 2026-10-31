"""Food roles, substitutions, Matvaretabellen candidate discovery and approvals.

Catalog words are a curated vocabulary, not nutrient observations. All
similar-name MVT matches remain suggestions until separately reviewed.
"""
from __future__ import annotations

import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

ROLES = frozenset({
    "main_meal_protein", "breakfast_protein", "quick_protein",
    "supporting_protein", "carb_base", "calorie_booster", "freezer_helper",
    # TASK-073: fruit, vegetables and cooking bases from real weekly offers.
    "fruit_berries", "vegetable", "cooking_base",
    # TASK-074: bread and sandwich toppings / spreads.
    "bread_base", "sandwich_topping",
})
TIERS = frozenset({"core", "alternative", "occasional", "supporting"})
FORMS = frozenset({"raw", "cooked", "as_sold", "drained", "dry", "unspecified"})
CONTEXTS = frozenset({
    "curry_stew", "mince_sauce", "tray_bake", "rice_bowl", "potato_bowl",
    "breakfast_oats",
})
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class FoodRoleError(ValueError):
    """The curated catalog is ambiguous or inconsistent."""


def _unique_strings(values, label: str) -> list[str]:
    if (not isinstance(values, list) or not values
            or any(not isinstance(v, str) or not v.strip() for v in values)
            or len(values) != len(set(values))):
        raise FoodRoleError(f"invalid or duplicate {label}")
    return values


def validate_catalog(payload: dict) -> None:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise FoodRoleError("unsupported food catalog version")
    items = payload.get("foods")
    rules = payload.get("substitutions")
    if not isinstance(items, list) or not items:
        raise FoodRoleError("foods must be a nonempty list")
    if not isinstance(rules, list):
        raise FoodRoleError("substitutions must be a list")
    keys: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise FoodRoleError("invalid food row")
        key = item.get("key")
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or key in keys:
            raise FoodRoleError(f"invalid or duplicate food key {key!r}")
        keys.add(key)
        if not isinstance(item.get("label"), str) or not item["label"].strip():
            raise FoodRoleError(f"missing label for {key}")
        if item.get("tier") not in TIERS or item.get("form_hint") not in FORMS:
            raise FoodRoleError(f"unsupported tier/form for {key}")
        if set(_unique_strings(item.get("roles"), f"roles of {key}")) - ROLES:
            raise FoodRoleError(f"unknown role for {key}")
        terms = _unique_strings(
            item.get("mvt_search_terms"), f"search terms of {key}"
        )
        if any(len(term) < 3 or "%" in term or "_" in term for term in terms):
            raise FoodRoleError(f"unsafe search term for {key}")
    seen: set[tuple[str, str, str]] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise FoodRoleError("invalid substitution")
        ctx, before, after = (
            rule.get("context"), rule.get("from"), rule.get("to")
        )
        identity = (ctx, before, after)
        if (ctx not in CONTEXTS or before not in keys or after not in keys
                or before == after or identity in seen):
            raise FoodRoleError(f"invalid substitution: {identity}")
        if rule.get("scope") not in {"contextual", "partial"}:
            raise FoodRoleError("substitution needs contextual or partial scope")
        note = rule.get("note")
        if not isinstance(note, str) or not note.strip():
            raise FoodRoleError("substitution requires an explicit caution")
        seen.add(identity)


def load_catalog(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_catalog(payload)
    return payload


def _existing_or_insert(db: sqlite3.Connection, table: str,
                        key_columns: tuple[str, ...],
                        row: dict[str, object]) -> None:
    """Immutable defaults: never overwrite personal reviews or changed rules."""
    key = tuple(row[col] for col in key_columns)
    where = " AND ".join(f"{col}=?" for col in key_columns)
    found = db.execute(f"SELECT * FROM {table} WHERE {where}", key).fetchone()
    columns = tuple(row)
    if found is not None:
        current = db.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE {where}", key
        ).fetchone()
        if current != tuple(row.values()):
            raise FoodRoleError(f"existing {table} {key} conflicts with defaults")
        return
    db.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(row.values()),
    )


def _migrate_role_check(db: sqlite3.Connection, schema_sql: str) -> None:
    """Rebuild food_role_assignments when its CHECK predates newer roles.

    SQLite cannot alter a CHECK constraint; rows are copied unchanged into a
    table created from the current schema (TASK-073/074 added roles).
    """
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                     "AND name='food_role_assignments'").fetchone()
    if row is None or all(f"'{role}'" in row[0] for role in ROLES):
        return
    create = re.search(r"CREATE TABLE IF NOT EXISTS food_role_assignments \((.*?)\n\);",
                       schema_sql, re.S)
    if create is None:
        raise FoodRoleError("schema has no food_role_assignments definition")
    with db:
        db.execute(f"CREATE TABLE food_role_assignments_v2 ({create.group(1)}\n)")
        db.execute("INSERT INTO food_role_assignments_v2 (food_key, role) "
                   "SELECT food_key, role FROM food_role_assignments")
        db.execute("DROP TABLE food_role_assignments")
        db.execute("ALTER TABLE food_role_assignments_v2 RENAME TO food_role_assignments")


def seed_catalog(db: sqlite3.Connection, payload: dict,
                 schema_path: Path) -> None:
    """Validate first; insert only missing defaults with rollback on conflict."""
    validate_catalog(payload)
    db.execute("PRAGMA foreign_keys = ON")
    schema_sql = schema_path.read_text(encoding="utf-8")
    _migrate_role_check(db, schema_sql)
    db.executescript(schema_sql)
    with db:
        for item in payload["foods"]:
            key = item["key"]
            _existing_or_insert(
                db, "food_role_items", ("food_key",),
                {"food_key": key, "food_label": item["label"],
                 "preference_tier": item["tier"], "form_hint": item["form_hint"]},
            )
            for role in item["roles"]:
                _existing_or_insert(
                    db, "food_role_assignments", ("food_key", "role"),
                    {"food_key": key, "role": role},
                )
            for term in item["mvt_search_terms"]:
                _existing_or_insert(
                    db, "food_role_search_terms", ("food_key", "search_term"),
                    {"food_key": key, "search_term": term},
                )
        for rule in payload["substitutions"]:
            _existing_or_insert(
                db, "food_substitution_rules",
                ("recipe_context", "from_food_key", "to_food_key"),
                {"recipe_context": rule["context"], "from_food_key": rule["from"],
                 "to_food_key": rule["to"], "caution": rule["note"],
                 "substitution_scope": rule["scope"]},
            )


def reference_suggestions(db: sqlite3.Connection, payload: dict,
                          *, limit_per_food: int = 3) -> list[tuple]:
    """Suggest comparable MVT names; do NOT persist/approve any match."""
    validate_catalog(payload)
    if limit_per_food < 1:
        raise FoodRoleError("limit_per_food must be positive")
    latest = db.execute(
        "SELECT id FROM mvt_snapshots WHERE locale='nb' "
        "ORDER BY retrieved_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if latest is None:
        return []
    rows = db.execute(
        "SELECT food_id, food_name FROM mvt_foods WHERE snapshot_id=?",
        (latest[0],),
    ).fetchall()
    output = []
    for item in payload["foods"]:
        found = []
        for food_id, food_name in rows:
            text = food_name.casefold()
            matches = [
                term for term in item["mvt_search_terms"]
                if term.casefold() in text
            ]
            if matches:
                # For display ordering only; NOT probability/confidence.
                found.append((max(map(len, matches)), food_id, food_name))
        for _, food_id, food_name in sorted(
            found, key=lambda match: (-match[0], match[1])
        )[:limit_per_food]:
            output.append((item["key"], item["label"], item["form_hint"],
                           latest[0], food_id, food_name, "UNREVIEWED"))
    return output


# ------------------------------------------------- reviewed reference foods
# TASK-070: an owner-reviewed food_key -> Matvaretabellen food association is
# the generic yardstick a branded candidate is compared with. Approving one
# records a human decision for ONE snapshot; it never edits MVT data and never
# makes a retail product, label or price verified.

def _latest_snapshot(db: sqlite3.Connection) -> int:
    row = db.execute(
        "SELECT id FROM mvt_snapshots WHERE locale='nb' "
        "ORDER BY retrieved_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise FoodRoleError("no Norwegian Matvaretabellen snapshot imported")
    return row[0]


def approve_reference(db: sqlite3.Connection, food_key: str, mvt_food_id: str,
                      note: str, *, reviewed_at: str,
                      approved_form: str | None = None) -> str:
    """Record one owner approval on the latest snapshot.

    Returns ``approved`` or ``unchanged`` (same food already approved).
    A different food for the same key and snapshot is refused: approvals are
    insert-only evidence, not a setting that is silently replaced.
    """
    if not isinstance(note, str) or not note.strip():
        raise FoodRoleError("an approval needs a review note")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        raise FoodRoleError("an approval needs a review time")
    snapshot = _latest_snapshot(db)
    item = db.execute("SELECT form_hint FROM food_role_items WHERE food_key=?",
                      (food_key,)).fetchone()
    if item is None:
        raise FoodRoleError(f"unknown food key {food_key!r} (seed the catalog first)")
    form = approved_form or item[0]
    if form not in FORMS:
        raise FoodRoleError(f"unsupported form {form!r}")
    food = db.execute("SELECT food_name FROM mvt_foods WHERE snapshot_id=? AND food_id=?",
                      (snapshot, mvt_food_id)).fetchone()
    if food is None:
        raise FoodRoleError(f"{mvt_food_id!r} is not in Matvaretabellen snapshot {snapshot}")
    existing = db.execute(
        "SELECT mvt_food_id, approved_form FROM food_reference_reviews "
        "WHERE food_key=? AND snapshot_id=?", (food_key, snapshot)).fetchone()
    if existing is not None:
        if existing == (mvt_food_id, form):
            return "unchanged"
        raise FoodRoleError(
            f"{food_key} already approved as {existing[0]} ({existing[1]}) "
            f"on snapshot {snapshot}; approvals are not replaced")
    with db:
        db.execute(
            "INSERT INTO food_reference_reviews (food_key, snapshot_id, mvt_food_id, "
            "reviewed_at, reviewer_note, approved_form) VALUES (?,?,?,?,?,?)",
            (food_key, snapshot, mvt_food_id, reviewed_at, note.strip(), form))
    return "approved"


def approved_references(db: sqlite3.Connection, query_path: Path) -> dict[str, dict]:
    """Approved references of the latest snapshot, keyed by food_key.

    Uses the checked-in portfolio query; protein/kcal are per 100 g edible
    part and None when the reference lacks them (never zero-filled).
    """
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='food_reference_reviews'"
                  ).fetchone() is None:
        return {}
    out = {}
    for row in db.execute(Path(query_path).read_text(encoding="utf-8")):
        (key, _label, _tier, form, food_id, food_name, protein, kcal,
         reviewed_at, _note, _retrieved) = row
        out[key] = {
            "food_key": key, "form": form, "mvt_food_id": food_id, "name": food_name,
            "protein_g": None if protein is None else Decimal(str(protein)),
            "kcal": None if kcal is None else Decimal(str(kcal)),
            "reviewed_at": reviewed_at}
    return out
