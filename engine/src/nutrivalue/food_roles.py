"""Food roles, substitutions and unapproved Matvaretabellen candidate discovery.

Catalog words are a curated vocabulary, not nutrient observations. All
similar-name MVT matches remain suggestions until separately reviewed.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

ROLES = frozenset({
    "main_meal_protein", "breakfast_protein", "quick_protein",
    "supporting_protein", "carb_base", "calorie_booster", "freezer_helper",
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


def seed_catalog(db: sqlite3.Connection, payload: dict,
                 schema_path: Path) -> None:
    """Validate first; insert only missing defaults with rollback on conflict."""
    validate_catalog(payload)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(schema_path.read_text(encoding="utf-8"))
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
