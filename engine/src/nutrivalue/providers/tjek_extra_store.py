"""TASK-032: select a PUBLIC Extra shop from an owner-private Tjek store listing.

The /v2/stores data contains public shop coordinates. These are NOT the
owner's home coordinates, and a shop geo catalog is still source_only,
not a verified shelf price or an official Coop region label.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
import unicodedata
from pathlib import Path

from nutrivalue.providers.tjek_weekly_collect import (
    CollectionError, _safe_id, _aware, _private_save, _week, PAGE_SIZE,
)
from nutrivalue.providers.tjek_geo_region import _candidates

DEALER = "80742m"
MAX_STORE_BYTES = 1_500_000


def _normalized(text: str) -> str:
    return " ".join(re.findall(
        r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold(),
        re.UNICODE,
    ))


def validate_public_store(record: object) -> dict:
    """Validate one public Oslo-area Extra shop location from Tjek's store API."""
    if not isinstance(record, dict) or record.get("dealer_id") != DEALER:
        raise CollectionError("invalid or foreign Extra shop metadata")
    store_id = _safe_id(record.get("id"))
    name = record.get("name")
    if not isinstance(name, str) or not 4 <= len(name) <= 200 or not name.strip():
        raise CollectionError("invalid public Extra shop name")
    latitude, longitude = record.get("latitude"), record.get("longitude")
    if (type(latitude) not in (int, float) or type(longitude) not in (int, float)
            or not 59.7 <= latitude <= 60.15
            or not 10.4 <= longitude <= 11.1):
        raise CollectionError("public Extra shop coordinates outside selected Oslo region")
    return {
        "store_id": store_id,
        "name": name,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "evidence": "owner-private archived Tjek public shop listing",
        "verified_shelf_price": False,
    }


def load_store_listing(*, stores_path: Path, private_root: Path) -> list[dict]:
    """Read the bounded owner-private saved public Extra shop listing offline."""
    root = Path(private_root).resolve(strict=True)
    if Path(stores_path).is_symlink():
        raise CollectionError("public store snapshot must not be a symlink")
    try:
        source = Path(stores_path).resolve(strict=True)
        rel = source.relative_to(root).parts
    except (OSError, ValueError) as exc:
        raise CollectionError("public Extra store listing outside private weekly archive") from exc
    if (not source.is_file() or len(rel) != 3
            or not re.fullmatch(r"\d{4}-W\d{2}", rel[0])
            or rel[2] != "oslo_stores.json" or rel[1] != "extra-api-investigation"):
        raise CollectionError("expected archived extra-api-investigation/oslo_stores.json")
    with source.open("rb") as handle:
        raw = handle.read(MAX_STORE_BYTES + 1)
    if not raw or len(raw) > MAX_STORE_BYTES:
        raise CollectionError("Extra store listing missing or oversized")
    try:
        records = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise CollectionError("Extra store listing invalid JSON") from exc
    if not isinstance(records, list) or not 1 <= len(records) < 100:
        raise CollectionError("Extra store listing invalid or capped at 100")
    seen = set()
    shops = []
    for record in records:
        store = validate_public_store(record)
        if store["store_id"] in seen:
            raise CollectionError("duplicate Extra shop in saved listing")
        seen.add(store["store_id"])
        shops.append(store)
    return shops


def load_public_extra_store(*, stores_path: Path, private_root: Path,
                            store_name: str) -> dict:
    """Select ONE public shop from the previously captured Tjek shops JSON."""
    if not isinstance(store_name, str) or not 5 <= len(store_name) <= 120:
        raise CollectionError("provide a specific Extra shop name")
    needle = _normalized(store_name)
    if len(needle) < 5:
        raise CollectionError("Extra shop name too short")
    matching = [
        store for store in load_store_listing(
            stores_path=stores_path, private_root=private_root,
        ) if (_normalized(store["name"]) == needle
              or _normalized(store["name"]).endswith(" " + needle))
    ]
    if len(matching) != 1:
        raise CollectionError(
            f"expected exactly one Extra shop match, found {len(matching)}")
    return matching[0]


def check_public_extra_store(*, private_root: Path, store: dict,
                             as_of: str, get_json,
                             city_reference_path: Path | None = None,
                             run_id: str | None = None,
                             captured_at: datetime | None = None) -> dict:
    """One owner-initiated /v2/catalogs GET at the chosen PUBLIC Extra shop.

    Retains immutable source and comparison; no offer fetches or user-home
    coordinates. Source city-centre response is optional independent context.
    """
    at = _aware(as_of)
    if not isinstance(store, dict) or store.get("evidence") != "owner-private archived Tjek public shop listing":
        raise CollectionError("Extra shop must come from private archived Tjek stores data")
    info = validate_public_store({
        "id": store.get("store_id"), "name": store.get("name"),
        "dealer_id": DEALER, "latitude": store.get("latitude"),
        "longitude": store.get("longitude"),
    })
    root = Path(private_root)
    weekdir = root / _week(at)
    if list(weekdir.glob("extra-store-check-*/manifest.json")):
        raise CollectionError("Extra shop geo probe already attempted this week")
    city_candidates = None
    if city_reference_path is not None:
        root_resolved = root.resolve(strict=True)
        candidate = Path(city_reference_path)
        if candidate.is_symlink():
            raise CollectionError("city reference JSON must not be a symlink")
        try:
            original = candidate.resolve(strict=True)
            parts = original.relative_to(root_resolved).parts
        except (OSError, ValueError) as exc:
            raise CollectionError("city catalog reference outside private archive") from exc
        if (len(parts) != 3 or parts[0] != _week(at)
                or parts[1] != "extra-api-investigation" or parts[2] != "oslo_catalogs.json"):
            raise CollectionError("expected original private Oslo catalogue reference")
        with original.open("rb") as h:
            raw = h.read(MAX_STORE_BYTES + 1)
        if not raw or len(raw) > MAX_STORE_BYTES:
            raise CollectionError("city catalogue reference too large")
        try:
            city_candidates, _ = _candidates(json.loads(raw), as_of=as_of)
        except (ValueError, UnicodeError) as exc:
            raise CollectionError("invalid saved Oslo reference JSON") from exc
    stamp = _aware((captured_at or datetime.now(timezone.utc)).isoformat()).astimezone(timezone.utc)
    run_id = run_id or stamp.strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", run_id):
        raise CollectionError("unsafe store probe run ID")
    folder = weekdir / f"extra-store-check-{run_id}"
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    halt = None
    result = None
    requests = 0
    try:
        requests = 1
        raw_catalogs = get_json("catalogs", {
            "dealer_ids": DEALER,
            "r_lat": info["latitude"], "r_lng": info["longitude"],
            "limit": PAGE_SIZE,
        })
        candidates, _ = _candidates(raw_catalogs, as_of=as_of)
        encoded = json.dumps(raw_catalogs, ensure_ascii=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        if not encoded or len(encoded) > MAX_STORE_BYTES:
            raise CollectionError("public Extra store catalog response oversized")
        _private_save(folder, "store.catalogs.json", encoded)
        selected = next(iter(candidates)) if len(candidates) == 1 else None
        result = {
            "status": "store_point_candidate" if selected else "store_point_unresolved",
            "public_shop_name": info["name"],
            "public_shop_id": info["store_id"],
            "catalog_id": selected,
            "current_local_candidates": len(candidates),
            "city_reference_comparison": (
                "same" if city_candidates == candidates else "different"
            ) if city_candidates is not None else "not_checked",
            "provider_catalog_selection": True,
            "verified_coop_ost_region": False,
            "verified_actual_shelf_price": False,
            "source_only": True,
            "raw_catalogs_sha256": hashlib.sha256(encoded).hexdigest(),
        }
    except CollectionError as exc:
        halt = str(exc)
    finally:
        manifest = {
            "schema_version": 1, "week": _week(at), "as_of": at.isoformat(),
            "captured_at": stamp.isoformat(), "run_directory": folder.name,
            "http_requests": requests, "max_requests": 1,
            "halted_reason": halt, "status": (
                "partial_or_unresolved" if halt else result["status"]),
            "source": "Tjek /v2/catalogs at public Extra shop location",
            "resolution": result,
        }
        _private_save(folder, "manifest.json", json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, indent=2,
        ).encode("utf-8"))
    return manifest
