"""TASK-032: bounded, independently checked Tjek geographic Extra selector.

Tjek's own ShopGun JS SDK sends r_lat/r_lng to /v2/catalogs. Owner's
one-off independent Oslo-vs-Bergen probe showed distinct local flyers.
This evidence validates a Tjek catalog at a *public city reference point*,
not a Tjek-to-Coop-Øst region assignment or shelf-price verification.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from nutrivalue.providers.tjek_weekly_collect import (
    PAGE_SIZE, CollectionError, _aware, _private_save, _safe_id, _week,
)

EXTRA = "80742m"
# Public city centres only. No user's precise location or home coordinates.
POINTS = {
    "oslo": (59.9139, 10.7522),
    "bergen": (60.39299, 5.32415),
}
MAX_GEO_GETS = 2
MAX_RAW_BYTES = 1_500_000
REPLAY_FILES = ("oslo_catalogs.json", "bergen_catalogs.json")
GEO_REPORT = "extra-geo-resolution-v1.json"


def _candidates(body: object, *, as_of: str) -> tuple[set[str], set[str]]:
    """Strict, complete local flyer selection, leaving zero-offer globals out."""
    when = _aware(as_of)
    if not isinstance(body, list) or len(body) >= PAGE_SIZE:
        raise CollectionError("geographic catalog listing invalid or provider cap reached")
    eligible: set[str] = set()
    all_ids: set[str] = set()
    for item in body:
        if not isinstance(item, dict) or item.get("dealer_id") != EXTRA:
            raise CollectionError("foreign or malformed Extra geographic catalog")
        ident = _safe_id(item.get("id"))
        if ident in all_ids:
            raise CollectionError("duplicate geographic catalog ID")
        all_ids.add(ident)
        count = item.get("offer_count")
        if count is not None and (type(count) is not int or not 0 <= count <= 5000):
            raise CollectionError("invalid geographic catalog offer_count")
        store_flag = item.get("all_stores")
        if type(store_flag) is not bool:
            raise CollectionError("geographic catalogue missing all_stores boolean")
        start, end = _aware(item.get("run_from")), _aware(item.get("run_till"))
        if end <= start:
            raise CollectionError("geographic catalog validity inverted")
        published = item.get("publish")
        if published is not None and _aware(published) > when:
            continue
        if start <= when < end and count and store_flag is False:
            eligible.add(ident)
    return eligible, all_ids


def geo_evidence(oslo: object, bergen: object, *, as_of: str,
                 oslo_alt: object | None = None) -> dict:
    """Decide only with two independent city centres; no forced selection."""
    local, ids_oslo = _candidates(oslo, as_of=as_of)
    control, ids_bergen = _candidates(bergen, as_of=as_of)
    if oslo_alt is not None:
        alt_local, alt_ids = _candidates(oslo_alt, as_of=as_of)
        if local != alt_local or ids_oslo != alt_ids:
            raise CollectionError("dealer_id and dealer_ids geo queries disagree")
    disjoint = bool(local and control and not (local & control))
    unique = len(local) == 1 and len(control) == 1 and disjoint
    all_stores_shared = len(ids_oslo & ids_bergen)
    return {
        "status": "geo_oslo_catalog_candidate" if unique else "geo_unresolved",
        "target": "Extra, public Oslo city centre",
        "dealer_id": EXTRA,
        "as_of": _aware(as_of).isoformat(),
        "week": _week(_aware(as_of)),
        "oslo_candidate_catalog_id": next(iter(local)) if unique else None,
        "bergen_control_catalog_id": next(iter(control)) if unique else None,
        "oslo_active_local_candidates": len(local),
        "bergen_active_local_candidates": len(control),
        "oslo_bergen_local_catalog_overlap": len(local & control),
        "shared_catalog_ids_in_responses": all_stores_shared,
        "two_parameter_forms_agree": oslo_alt is not None,
        "geographical_api_filter_observed": unique,
        "coop_ost_identity_verified": False,
        "local_store_advertisement_verified": False,
        "retail_shelf_price_verified": False,
        "source_only": True,
        "note": (
            "Catalog selection uses Tjek geofilter at public city centres; "
            "it does not by itself establish which Coop-operated region or "
            "specific retailer store the flyer officially belongs to. "
            "Zero-offer all_stores magazines cannot be a local offer candidate."
        ),
    }


def _encode(body: object) -> bytes:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    if not raw or len(raw) > MAX_RAW_BYTES:
        raise CollectionError("geographic Tjek metadata invalid or too large")
    return raw


def replay_geo(*, private_root: Path, input_dir: Path,
               as_of: str) -> dict:
    """Zero-network replay of user-owned probe, append-only report."""
    root = Path(private_root).resolve(strict=True)
    source = Path(input_dir)
    if source.is_symlink():
        raise CollectionError("geo replay folder must not be symlink")
    try:
        folder = source.resolve(strict=True)
        parts = folder.relative_to(root).parts
    except (OSError, ValueError) as exc:
        raise CollectionError("geo archive outside private root") from exc
    if (len(parts) != 2 or parts[0] != _week(_aware(as_of))
            or parts[1] != "extra-api-investigation"):
        raise CollectionError("expected saved week/extra-api-investigation")
    bodies = {}
    hashes = {}
    for name, filename in (("oslo", REPLAY_FILES[0]), ("bergen", REPLAY_FILES[1]),
                           ("oslo_alt", "oslo_sdk_filter.json")):
        source_path = folder / filename
        if name == "oslo_alt" and not source_path.exists():
            continue
        if source_path.is_symlink() or not source_path.is_file():
            raise CollectionError("missing or symlinked geographic JSON snapshot")
        with source_path.open("rb") as stream:
            raw = stream.read(MAX_RAW_BYTES + 1)
        if not raw or len(raw) > MAX_RAW_BYTES:
            raise CollectionError("saved geo JSON too large or empty")
        try:
            body = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise CollectionError("saved geographic JSON invalid") from exc
        bodies[name] = body
        hashes[name] = hashlib.sha256(raw).hexdigest()
    result = geo_evidence(bodies["oslo"], bodies["bergen"], as_of=as_of,
                          oslo_alt=bodies.get("oslo_alt"))
    result.update({
        "schema_version": 1,
        "mode": "existing_private_probe_offline",
        "source_sha256": hashes,
        "http_requests": 0,
        "raw_source_preserved": True,
    })
    report_path = folder / GEO_REPORT
    if report_path.exists() or report_path.is_symlink():
        raise CollectionError("geo replay already attempted; never overwrite")
    _private_save(folder, GEO_REPORT, json.dumps(
        result, sort_keys=True, ensure_ascii=False, indent=2,
    ).encode("utf-8"))
    return result


def capture_geo(*, private_root: Path, as_of: str, get_json,
                captured_at: datetime | None = None,
                run_id: str | None = None) -> dict:
    """One fresh geo-scoped Extra observation/week, <=2 paced metadata GETs.

    No offers or store-personal locations are requested. The source replies
    are private append-only snapshots; request failure stops further GETs.
    """
    at = _aware(as_of)
    week_dir = Path(private_root) / _week(at)
    if list(week_dir.glob("extra-geo-*/manifest.json")):
        raise CollectionError("geographic probe already attempted this week")
    stamp = _aware((captured_at or datetime.now(timezone.utc)).isoformat()).astimezone(timezone.utc)
    run_id = run_id or stamp.strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", run_id):
        raise CollectionError("unsafe geo run identifier")
    folder = week_dir / f"extra-geo-{run_id}"
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    bodies = {}
    hashes = {}
    requests = 0
    halt = None
    result = None
    try:
        for city in ("oslo", "bergen"):
            lat, long = POINTS[city]
            requests += 1
            body = get_json("catalogs", {
                "dealer_ids": EXTRA, "r_lat": lat, "r_lng": long,
                "limit": PAGE_SIZE,
            })
            # Validate before saving; any 429/error stops rather than retries.
            _candidates(body, as_of=as_of)
            data = _encode(body)
            filename = f"{city}.catalogs.json"
            _private_save(folder, filename, data)
            bodies[city] = body
            hashes[city] = hashlib.sha256(data).hexdigest()
        result = geo_evidence(bodies["oslo"], bodies["bergen"], as_of=as_of)
    except CollectionError as exc:
        halt = str(exc)
    finally:
        report = {
            "schema_version": 1,
            "week": _week(at), "as_of": at.isoformat(),
            "fetched_at": stamp.isoformat(), "run_directory": folder.name,
            "http_requests": requests, "max_requests": MAX_GEO_GETS,
            "halted_reason": halt, "status": (
                "partial_or_unresolved" if halt else result["status"]),
            "source_sha256": hashes, "resolution": result,
            "private_only": True,
        }
        _private_save(folder, "manifest.json", json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2,
        ).encode("utf-8"))
    return report
