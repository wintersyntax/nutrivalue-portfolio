"""TASK-052: import saved weekly Tjek collector runs into a PRIVATE offer DB.

Builds on TASK-023 staging (``tjek_snapshots`` / ``tjek_catalogs`` /
``tjek_offers``) and adds run coverage, acquisition path (listing vs.
hotspot detail, printed page), TASK-033 condition interpretation and
TASK-041 category tags. Offline only: reads saved files, makes no HTTP
requests and never promotes a price beyond ``source_only``.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from nutrivalue.offer_categories import TAXONOMY_VERSION, classify_offer, pack_grams
from nutrivalue.providers.tjek_offer_conditions import interpret_offer
from nutrivalue.providers.tjek_offline import (
    CHAINS, MAX_FILE_BYTES, Snapshot, TjekSnapshotError, insert_snapshot, parse_snapshot,
)

CONDITIONS_PARSER_VERSION = "tjek_offer_conditions@1"
LIMITED_STOCK = re.compile(
    r"\bpartivare\b|så\s+langt\s+lager(?:et)?\s+rekker|begrenset\s+(?:parti|antall)", re.I)
WEEK_DIR = re.compile(r"\d{4}-W\d{2}")
RUN_DIR = re.compile(r"run-[A-Za-z0-9_-]{8,40}")
MAX_MANIFEST_BYTES = 1_000_000
MAX_HOTSPOT_BYTES = 8_000_000


class OfferImportError(ValueError):
    """A saved run cannot be imported without guessing."""


def _read(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise OfferImportError(f"{path.name} must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise OfferImportError(f"{path.name} is empty or oversized")
    return raw


def _json(raw: bytes, name: str) -> object:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise OfferImportError(f"{name} is not valid JSON") from exc


def _original_offers(snapshot: Snapshot) -> dict[tuple[str, str], dict]:
    envelope = json.loads(snapshot.raw_payload)
    entries = envelope.get("entries") if isinstance(envelope, dict) else None
    if not isinstance(entries, list):
        raise OfferImportError("weekly snapshot must use the collector 'entries' envelope")
    found = {}
    for entry in entries:
        catalog_id = entry["catalog"]["id"]
        for offer in entry["offers"]:
            found[(catalog_id, offer["id"])] = offer
    return found


def _acquisition(spots: dict | None, snapshot: Snapshot, hotspots_enabled: bool):
    """Map (catalog, offer) -> (acquisition, pages_json, locations_json)."""
    rows = {}
    keys = {(row[1], row[2]) for row in snapshot.offers}
    if not hotspots_enabled:
        return {key: ("listing", None, None) for key in keys}
    if spots is None:
        raise OfferImportError("run enabled hotspots but the chain hotspot file is missing")
    catalogs = spots.get("catalogs") if isinstance(spots, dict) else None
    if not isinstance(catalogs, list):
        raise OfferImportError("hotspot file lacks catalogs")
    for cat in catalogs:
        cid = cat.get("catalog_id")
        listing = set(cat.get("listing_offer_ids") or [])
        recovered = set(cat.get("detail_recovered_ids") or [])
        if listing & recovered:
            raise OfferImportError("offer recorded as both listing and recovered")
        pages = cat.get("hotspot_offer_pages") or {}
        locations = cat.get("hotspot_offer_locations") or {}
        for oid in listing | recovered:
            rows[(cid, oid)] = (
                "hotspot_detail" if oid in recovered else "listing",
                json.dumps(pages[oid]) if oid in pages else None,
                json.dumps(locations[oid], sort_keys=True) if oid in locations else None,
            )
    if set(rows) != keys:
        raise OfferImportError("hotspot file does not account for exactly the saved offers")
    return rows


def import_run(run_dir: Path, db_path: Path, schema_dir: Path) -> dict:
    """Validate one saved run completely, then write it in ONE transaction.

    Re-importing an identical run is a no-op; a changed manifest for an
    already imported run fails closed instead of overwriting history.
    """
    run_dir = Path(run_dir)
    if not RUN_DIR.fullmatch(run_dir.name) or not WEEK_DIR.fullmatch(run_dir.parent.name):
        raise OfferImportError("expected a YYYY-Www/run-* collector directory")
    run_id = f"{run_dir.parent.name}/{run_dir.name}"
    manifest_raw = _read(run_dir / "manifest.json", MAX_MANIFEST_BYTES)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    manifest = _json(manifest_raw, "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("chains"), list):
        raise OfferImportError("manifest lacks chain records")
    if manifest.get("week") != run_dir.parent.name:
        raise OfferImportError("manifest week does not match directory")
    hotspots_enabled = bool(manifest.get("hotspots_enabled", False))

    chains = []
    for record in manifest["chains"]:
        dealer = record.get("dealer_id")
        if dealer not in CHAINS:
            raise OfferImportError("manifest contains an unknown dealer")
        snapshot = acquisition = originals = None
        declared = None
        if record.get("snapshot_file"):
            name = record["snapshot_file"]
            if name != f"{dealer}.json":
                raise OfferImportError("unexpected snapshot file name")
            raw = _read(run_dir / name, MAX_FILE_BYTES)
            if hashlib.sha256(raw).hexdigest() != record.get("snapshot_sha256"):
                raise OfferImportError(f"{name} does not match its manifest SHA-256")
            try:
                snapshot = parse_snapshot(raw, name)
            except TjekSnapshotError as exc:
                raise OfferImportError(f"{name}: {exc}") from exc
            if {c[2] for c in snapshot.catalogs} != {dealer}:
                raise OfferImportError(f"{name} contains another dealer")
            declared = sum(c[8] or 0 for c in snapshot.catalogs)
            spot_file = run_dir / f"{dealer}.hotspots.json"
            spots = (_json(_read(spot_file, MAX_HOTSPOT_BYTES), spot_file.name)
                     if spot_file.exists() else None)
            acquisition = _acquisition(spots, snapshot, hotspots_enabled)
            originals = _original_offers(snapshot)
        chains.append((record, snapshot, declared, acquisition, originals))

    now = datetime.now(timezone.utc).isoformat()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript((schema_dir / "tjek_offer_staging.sql").read_text(encoding="utf-8"))
        db.executescript((schema_dir / "offer_database.sql").read_text(encoding="utf-8"))
        with db:
            known = db.execute("SELECT manifest_sha256 FROM tjek_runs WHERE run_id=?",
                               (run_id,)).fetchone()
            if known:
                if known[0] != manifest_sha:
                    raise OfferImportError("run already imported with a different manifest")
                return {"run_id": run_id, "imported": False,
                        "offers": manifest.get("total_saved_offers", 0)}
            db.execute("""INSERT INTO tjek_runs (run_id,week,fetched_at,as_of,manifest_sha256,
                          batch_status,halted_reason,http_requests,hotspots_enabled,
                          total_saved_offers,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (run_id, manifest["week"], manifest["fetched_at"], manifest["as_of"],
                        manifest_sha, manifest["batch_status"], manifest.get("halted_reason"),
                        manifest["http_requests"], int(hotspots_enabled),
                        manifest["total_saved_offers"], now))
            offers = 0
            for record, snapshot, declared, acquisition, originals in chains:
                if snapshot is not None:
                    insert_snapshot(db, snapshot)
                    for row in snapshot.offers:
                        sha, cid, oid = row[0], row[1], row[2]
                        heading, description = row[4], row[5]
                        kind, pages, locations = acquisition[(cid, oid)]
                        db.execute("""INSERT OR IGNORE INTO tjek_offer_acquisition
                                      VALUES (?,?,?,?,?,?)""",
                                   (sha, cid, oid, kind, pages, locations))
                        cond = interpret_offer(originals[(cid, oid)])
                        db.execute("""INSERT OR IGNORE INTO tjek_offer_conditions
                                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (sha, cid, oid, CONDITIONS_PARSER_VERSION,
                                    cond["price_kind"], cond["explicit_member_ore"],
                                    cond["conditional_member_ore"],
                                    cond["review_only_possible_member_ore"],
                                    cond["explicit_nonmember_ore"],
                                    cond["explicit_previous_ore"], cond["saving_ore"],
                                    cond["bundle_units"], cond["bundle_total_ore"],
                                    int(cond["bundle_member_only"]),
                                    cond["percent_condition_count"],
                                    int(cond["pant_mentioned"]),
                                    int(bool(LIMITED_STOCK.search(
                                        f"{heading} {description or ''}"))),
                                    json.dumps(cond["review_reasons"])))
                        _insert_category(db, sha, cid, oid, classify_offer(
                            heading, description, pack_grams(row[9], row[10], row[11])))
                    offers += len(snapshot.offers)
                db.execute("""INSERT INTO tjek_run_chains VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (run_id, record["dealer_id"], record["chain"], record["status"],
                            record["catalogs_selected"], declared, record["offers_saved"],
                            record.get("listing_offers"), record.get("hotspot_offers"),
                            record.get("detail_recovered"),
                            record["source_count_shortfall"],
                            snapshot.sha256 if snapshot is not None else None))
            if offers != manifest["total_saved_offers"]:
                raise OfferImportError("saved offers do not match manifest total")
    return {"run_id": run_id, "imported": True, "offers": offers,
            "chains": sum(1 for c in chains if c[1] is not None)}


def _insert_category(db: sqlite3.Connection, sha: str, cid: str, oid: str, cat) -> None:
    db.execute("""INSERT OR IGNORE INTO tjek_offer_categories
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
               (sha, cid, oid, cat.taxonomy_version, cat.status,
                cat.food_category, cat.food_key, cat.form, cat.reason,
                json.dumps(list(cat.category_tags)), cat.offer_kind, cat.tag_status))


def reclassify(db_path: Path) -> int:
    """Add current-taxonomy categories for offers imported under an older one.

    Append-only: earlier versions stay as they were and the views read the
    newest version, so a classifier change (TASK-073 taxonomy v4) reaches
    earlier weeks without touching their source rows. Returns rows added.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return 0
    with sqlite3.connect(db_path) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='tjek_offer_categories'").fetchone() is None:
            return 0
        rows = db.execute(
            """SELECT o.snapshot_sha256, o.catalog_id, o.offer_id, o.heading, o.description,
                      o.quantity, o.unit, o.pack_count
               FROM tjek_offers o
               WHERE EXISTS (SELECT 1 FROM tjek_offer_categories k
                             WHERE k.snapshot_sha256=o.snapshot_sha256
                               AND k.catalog_id=o.catalog_id AND k.offer_id=o.offer_id)
                 AND NOT EXISTS (SELECT 1 FROM tjek_offer_categories k
                                 WHERE k.snapshot_sha256=o.snapshot_sha256
                                   AND k.catalog_id=o.catalog_id AND k.offer_id=o.offer_id
                                   AND k.taxonomy_version=?)""",
            (TAXONOMY_VERSION,)).fetchall()
        with db:
            for sha, cid, oid, heading, description, quantity, unit, count in rows:
                _insert_category(db, sha, cid, oid, classify_offer(
                    heading, description, pack_grams(quantity, unit, count)))
    return len(rows)


def discover_runs(weekly_root: Path) -> list[Path]:
    """All collector run directories with a manifest, oldest first."""
    root = Path(weekly_root)
    runs = [p for p in root.glob("*/run-*")
            if WEEK_DIR.fullmatch(p.parent.name) and RUN_DIR.fullmatch(p.name)
            and (p / "manifest.json").is_file()]
    return sorted(runs, key=lambda p: (p.parent.name, p.name))
