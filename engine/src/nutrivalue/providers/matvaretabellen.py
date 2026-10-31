"""Version-aware local import of official Matvaretabellen reference nutrition.

No food row becomes a sellable SKU; no price observations are added.
Endpoint field names were verified against Mattilsynet/matvaretabellen-deux
src/matvaretabellen/pages/api.clj and its JSON API tests (2026-10-10).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_FILE = ROOT / "analytics" / "matvaretabellen_schema.sql"
BASE_URL = "https://www.matvaretabellen.no"
ENDPOINTS = ("foods", "food-groups", "nutrients", "sources")
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class MatvaretabellenError(ValueError):
    """Invalid, incomplete or unavailable external dataset."""


def urls_for(locale: str) -> dict[str, str]:
    if locale not in ("nb", "en"):
        raise MatvaretabellenError("locale must be nb or en")
    return {kind: f"{BASE_URL}/api/{locale}/{kind}.json" for kind in ENDPOINTS}


def _read_limited_response(response) -> bytes:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise MatvaretabellenError("Official response exceeded 32 MiB safety limit")
    return raw


def fetch_payloads(locale: str, *, input_dir: Path | None = None, opener=urlopen) -> dict[str, bytes]:
    """Read four official JSON payloads; input_dir allows reproducible offline runs."""
    urls = urls_for(locale)
    result = {}
    for kind, url in urls.items():
        if input_dir is not None:
            try:
                raw = (input_dir / f"{kind}.json").read_bytes()
            except OSError as exc:
                raise MatvaretabellenError(f"Cannot read {kind}.json from input directory") from exc
            if len(raw) > MAX_RESPONSE_BYTES:
                raise MatvaretabellenError(f"{kind}.json exceeds 32 MiB")
        else:
            req = Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "NutriValue/0.1 (personal non-commercial nutrition research)",
            })
            try:
                with opener(req, timeout=60) as response:
                    raw = _read_limited_response(response)
            except HTTPError as exc:
                raise MatvaretabellenError(f"Matvaretabellen {kind} returned HTTP {exc.code}") from None
            except (URLError, TimeoutError, OSError):
                raise MatvaretabellenError(f"Could not download Matvaretabellen {kind}") from None
        if not raw:
            raise MatvaretabellenError(f"Empty {kind} payload")
        result[kind] = raw
    return result


def _parse(raw: bytes, kind: str, locale: str) -> list[dict]:
    try:
        doc = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeError, ValueError) as exc:
        raise MatvaretabellenError(f"Invalid JSON in {kind}") from exc
    key = {"foods": "foods", "food-groups": "foodGroups",
           "nutrients": "nutrients", "sources": "sources"}[kind]
    if not isinstance(doc, dict) or not isinstance(doc.get(key), list) or not doc[key]:
        raise MatvaretabellenError(f"{kind}: expected non-empty {key} array")
    if doc.get("locale") != locale:
        raise MatvaretabellenError(f"{kind}: locale differs from {locale}")
    if any(not isinstance(row, dict) for row in doc[key]):
        raise MatvaretabellenError(f"{kind}: row must be a JSON object")
    return doc[key]


def _name(row: dict, field: str, location: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MatvaretabellenError(f"{location}: required {field} is absent")
    return value.strip()


def _decimal(value, location: str, *, lower=Decimal("0"),
             upper=None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise MatvaretabellenError(f"{location}: boolean is not numeric")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MatvaretabellenError(f"{location}: invalid number") from exc
    if not d.is_finite() or d < lower or (upper is not None and d > upper):
        raise MatvaretabellenError(f"{location}: out-of-range number")
    return str(d)


def _measurement(row: dict, key: str, unit: str, where: str) -> str | None:
    item = row.get(key)
    if item is None:
        return None
    if not isinstance(item, dict):
        raise MatvaretabellenError(f"{where}: {key} must be an object")
    # Mattilsynet uses a source-only object when a food's energy value
    # is unknown. A missing quantity has no numeric unit to validate.
    # An explicit unit, when present, must still be correct.
    if item.get("unit") not in (None, unit):
        raise MatvaretabellenError(f"{where}: {key} unit must be {unit}")
    source_id = item.get("sourceId")
    if source_id is not None and not isinstance(source_id, str):
        raise MatvaretabellenError(f"{where}: invalid {key} sourceId")
    if item.get("quantity") is None:
        return None
    if item.get("unit") != unit:
        raise MatvaretabellenError(f"{where}: {key} unit must be {unit}")
    return _decimal(item["quantity"], f"{where}.{key}")


def _rows(payloads: dict[str, bytes], locale: str) -> dict[str, list[tuple]]:
    if set(payloads) != set(ENDPOINTS):
        raise MatvaretabellenError("Expected all four Matvaretabellen endpoints")
    parsed = {kind: _parse(payloads[kind], kind, locale) for kind in ENDPOINTS}
    groups, nutrients, sources, foods, values, missing, missing_energy = [], [], [], [], [], [], []
    seen_groups, seen_nutrients, seen_sources, seen_foods, seen_pairs = set(), set(), set(), set(), set()

    for row in parsed["food-groups"]:
        key = _name(row, "foodGroupId", "food group")
        if key in seen_groups:
            raise MatvaretabellenError(f"Duplicate food group {key}")
        seen_groups.add(key)
        parent = row.get("parentId")
        if parent is not None and not isinstance(parent, str):
            raise MatvaretabellenError(f"{key}: invalid group parent")
        groups.append((key, _name(row, "name", key), parent))

    for row in parsed["nutrients"]:
        key = _name(row, "nutrientId", "nutrient")
        if key in seen_nutrients:
            raise MatvaretabellenError(f"Duplicate nutrient {key}")
        seen_nutrients.add(key)
        precision = row.get("decimalPrecision")
        if precision is not None and (type(precision) is not int or not 0 <= precision <= 8):
            raise MatvaretabellenError(f"{key}: invalid nutrient precision")
        eid = row.get("euroFirId")
        nutrients.append((key, _name(row, "name", key), _name(row, "unit", key),
                          eid if isinstance(eid, str) else None, precision))

    if "Protein" not in seen_nutrients:
        raise MatvaretabellenError("Official nutrient definitions lack Protein")
    for row in parsed["sources"]:
        key = _name(row, "sourceId", "source")
        if key in seen_sources:
            raise MatvaretabellenError(f"Duplicate source {key}")
        seen_sources.add(key)
        description = row.get("description")
        sources.append((key, description if isinstance(description, str) else None))

    for row in parsed["foods"]:
        food_id = _name(row, "foodId", "food")
        if food_id in seen_foods:
            raise MatvaretabellenError(f"Duplicate food {food_id}")
        seen_foods.add(food_id)
        group_id = _name(row, "foodGroupId", food_id)
        if group_id not in seen_groups:
            raise MatvaretabellenError(f"{food_id}: unknown food group {group_id}")
        uri = row.get("uri")
        if uri is not None and (not isinstance(uri, str) or not uri.startswith("https://")):
            raise MatvaretabellenError(f"{food_id}: invalid source URI")
        edible = row.get("ediblePart")
        edible_pct = None
        if edible is not None:
            if not isinstance(edible, dict):
                raise MatvaretabellenError(f"{food_id}: invalid ediblePart")
            edible_pct = _decimal(edible.get("percent"), f"{food_id}.ediblePart",
                                  upper=Decimal("100"))
        kcal = _measurement(row, "calories", "kcal", food_id)
        kj = _measurement(row, "energy", "kJ", food_id)
        foods.append((
            food_id, _name(row, "foodName", food_id), group_id, uri,
            edible_pct, kcal, kj,
        ))
        for field, expected_unit in (("calories", "kcal"), ("energy", "kJ")):
            item = row.get(field)
            if isinstance(item, dict) and item.get("quantity") is None:
                # Provenance for an unknown energy quantity stays separate:
                # NULL != zero, and no conversion is inferred.
                missing_energy.append((food_id, expected_unit, item.get("sourceId")))
        constituents = row.get("constituents")
        if not isinstance(constituents, list):
            raise MatvaretabellenError(f"{food_id}: constituents must be an array")
        for part in constituents:
            if not isinstance(part, dict):
                raise MatvaretabellenError(f"{food_id}: invalid constituent")
            nid = _name(part, "nutrientId", food_id)
            pair = (food_id, nid)
            if pair in seen_pairs:
                raise MatvaretabellenError(f"{food_id}: duplicate nutrient {nid}")
            seen_pairs.add(pair)
            # The official nutrients endpoint includes only measured nutrients.
            # The foods endpoint may also include source-only BIOT etc.
            # Preserve those separately, not as fabricated zero or definition.
            src = part.get("sourceId")
            if src is not None and not isinstance(src, str):
                raise MatvaretabellenError(f"{food_id}.{nid}: invalid sourceId")
            if part.get("quantity") is None:
                missing.append((food_id, nid, src))
                continue
            if nid not in seen_nutrients:
                raise MatvaretabellenError(f"{food_id}: unknown measured nutrient {nid}")
            unit = _name(part, "unit", f"{food_id}.{nid}")
            standard_unit = next(n[2] for n in nutrients if n[0] == nid)
            if unit != standard_unit:
                raise MatvaretabellenError(f"{food_id}.{nid}: mismatched nutrient unit")
            quantity = _decimal(part["quantity"], f"{food_id}.{nid}")
            values.append((food_id, nid, quantity, unit, src))
    return {"groups": groups, "nutrients": nutrients, "sources": sources,
            "foods": foods, "values": values, "missing": missing,
            "missing_energy": missing_energy}


def import_payloads(db_path: Path, payloads: dict[str, bytes],
                    *, locale: str = "nb", retrieved_at: str | None = None) -> dict:
    urls = urls_for(locale)
    parsed = _rows(payloads, locale)  # validate entire snapshot before touching DB
    hashes = {key: hashlib.sha256(payloads[key]).hexdigest() for key in ENDPOINTS}
    combined = hashlib.sha256("".join(f"{k}:{hashes[k]}\n" for k in sorted(hashes)).encode()).hexdigest()
    fetched = retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not isinstance(fetched, str) or not fetched.strip():
        raise MatvaretabellenError("retrieved_at must be an ISO timestamp")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(db_path) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            existing = db.execute(
                "SELECT id FROM mvt_snapshots WHERE locale=? AND content_sha256=?",
                (locale, combined),
            ).fetchone()
            if existing:
                return {"status": "unchanged", "snapshot_id": existing[0],
                        "foods": len(parsed["foods"]), "nutrients": len(parsed["nutrients"]),
                        "measurements": len(parsed["values"]),
                        "unquantified": len(parsed["missing"]),
                        "unquantified_energy": len(parsed["missing_energy"]),
                        "sha256": combined}
            with db:
                cursor = db.execute(
                    "INSERT INTO mvt_snapshots(locale,content_sha256,retrieved_at) VALUES(?,?,?)",
                    (locale, combined, fetched),
                )
                sid = cursor.lastrowid
                db.executemany(
                    "INSERT INTO mvt_endpoint_provenance VALUES(?,?,?,?)",
                    ((sid, kind, urls[kind], hashes[kind]) for kind in ENDPOINTS),
                )
                db.executemany(
                    "INSERT INTO mvt_food_groups VALUES(?,?,?,?)",
                    ((sid, *x) for x in parsed["groups"]),
                )
                db.executemany(
                    "INSERT INTO mvt_nutrients VALUES(?,?,?,?,?,?)",
                    ((sid, *x) for x in parsed["nutrients"]),
                )
                db.executemany(
                    "INSERT INTO mvt_sources VALUES(?,?,?)",
                    ((sid, *x) for x in parsed["sources"]),
                )
                db.executemany(
                    "INSERT INTO mvt_foods VALUES(?,?,?,?,?,?,?,?)",
                    ((sid, *x) for x in parsed["foods"]),
                )
                db.executemany(
                    "INSERT INTO mvt_food_nutrients VALUES(?,?,?,?,?,?)",
                    ((sid, *x) for x in parsed["values"]),
                )
                db.executemany(
                    "INSERT INTO mvt_unquantified_nutrients VALUES(?,?,?,?)",
                    ((sid, *x) for x in parsed["missing"]),
                )
                db.executemany(
                    "INSERT INTO mvt_unquantified_energy VALUES(?,?,?,?)",
                    ((sid, *x) for x in parsed["missing_energy"]),
                )
    except sqlite3.Error as exc:
        raise MatvaretabellenError("SQLite import failed; no partial snapshot accepted") from exc
    return {"status": "imported", "snapshot_id": sid,
            "foods": len(parsed["foods"]), "nutrients": len(parsed["nutrients"]),
            "measurements": len(parsed["values"]),
            "unquantified": len(parsed["missing"]),
            "unquantified_energy": len(parsed["missing_energy"]),
            "sha256": combined}
