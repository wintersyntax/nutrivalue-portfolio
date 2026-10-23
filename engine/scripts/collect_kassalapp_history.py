#!/usr/bin/env python3
"""Stage a bounded Kassalapp EAN daily price history in PRIVATE SQLite.

The source returns provider-reported DAILY aggregates, not verified shelf
prices or time-scoped offers. This script never writes to curated ranking
tables. No automatic EAN/nutrition match or package mass is inferred.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.kassalapp import (  # noqa: E402
    BASE_URL, KassalappClient, KassalappError,
)

SOURCE = BASE_URL + "/products/prices-bulk"
DEFAULT_DB = ROOT / "data/private/nutrivalue.sqlite3"


def validate_eans(eans: list[str], days: int) -> list[str]:
    if (type(days) is not int or not 1 <= days <= 90
            or not isinstance(eans, list) or not 1 <= len(eans) <= 100
            or any(not isinstance(ean, str) or not ean.isascii()
                   or not ean.isdigit() or not 8 <= len(ean) <= 14
                   for ean in eans)
            or len(set(eans)) != len(eans)):
        raise ValueError("Provide 1–100 distinct 8–14 digit EANs and 1–90 days")
    return sorted(eans)


def _price_ore(value) -> int:
    if value is None or isinstance(value, bool):
        raise ValueError("History price is missing or invalid")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("History price is not numeric") from exc
    ore = amount * 100
    if not ore.is_finite() or ore <= 0 or ore != ore.to_integral_value():
        raise ValueError("History price must be a positive NOK amount in whole øre")
    return int(ore)


def _checked_at(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid latest store checked-at")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid latest store checked-at") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Store checked-at must include a timezone")
    return value


def _normalize_response(payload: object, requested: set[str]) -> list[tuple]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Bulk history must contain a data array")
    rows: list[tuple] = []
    products: set[str] = set()
    distinct: set[tuple[str, str, str]] = set()
    for product in payload["data"]:
        if not isinstance(product, dict):
            raise ValueError("Invalid bulk product")
        raw_ean = product.get("ean")
        ean = str(raw_ean) if not isinstance(raw_ean, bool) and raw_ean is not None else ""
        if ean not in requested or ean in products:
            raise ValueError("Unexpected or duplicate EAN in bulk response")
        products.add(ean)
        stores = product.get("stores") or []
        history = product.get("price_history") or []
        if not isinstance(stores, list) or not isinstance(history, list):
            raise ValueError("Invalid store/history collection")
        latest_by_store: dict[str, str | None] = {}
        for store in stores:
            if not isinstance(store, dict) or not isinstance(store.get("store"), str):
                raise ValueError("Invalid latest store entry")
            code = store["store"].strip()
            if not code or code in latest_by_store:
                raise ValueError("Duplicate or missing store code")
            latest_by_store[code] = _checked_at(store.get("last_checked"))
        for item in history:
            if not isinstance(item, dict):
                raise ValueError("Invalid daily history row")
            store_code = item.get("store")
            price_day = item.get("date")
            if not isinstance(store_code, str) or not store_code.strip():
                raise ValueError("Missing daily history store")
            if not isinstance(price_day, str):
                raise ValueError("Missing daily history date")
            try:
                day = date.fromisoformat(price_day)
            except ValueError as exc:
                raise ValueError("Malformed daily history date") from exc
            if day.isoformat() != price_day:
                raise ValueError("Noncanonical daily history date")
            code = store_code.strip()
            key = (ean, code, price_day)
            if key in distinct:
                raise ValueError("Duplicate daily EAN/store/date aggregate")
            distinct.add(key)
            rows.append((
                ean, code, price_day, _price_ore(item.get("price")),
                latest_by_store.get(code),
            ))
    return rows


def stage_history(
    payload: dict,
    eans: list[str],
    db_path: Path,
    *,
    days: int = 30,
    aggregation: str = "min",
    fetched_at: str | None = None,
) -> tuple[int, int, bool]:
    """Validate complete provider response BEFORE any SQL change.

    Returns (returned_products, history_rows, created_new_snapshot).
    An identical provider payload with identical query scope is idempotent.
    """
    requested = validate_eans(eans, days)
    if aggregation != "min":
        raise ValueError("Only the live-tested daily min aggregation is supported")
    rows = _normalize_response(payload, set(requested))
    stamp = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    _checked_at(stamp)  # timezone is compulsory for collection timestamps
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    requested_json = json.dumps(requested, separators=(",", ":"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript((ROOT / "analytics/schema.sql").read_text(encoding="utf-8"))
        db.execute("PRAGMA foreign_keys = ON")
        cur = db.execute(
            """INSERT OR IGNORE INTO kassalapp_history_snapshots
            (requested_eans_json, days, aggregation, payload_sha256, fetched_at,
             source_ref, raw_response_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (requested_json, days, aggregation, digest, stamp, SOURCE, raw),
        )
        created = cur.rowcount == 1
        snap = db.execute(
            """SELECT id, raw_response_json FROM kassalapp_history_snapshots
            WHERE requested_eans_json=? AND days=? AND aggregation=?
              AND payload_sha256=?""",
            (requested_json, days, aggregation, digest),
        ).fetchone()
        if snap is None or snap[1] != raw:
            raise ValueError("Snapshot identity collision or missing snapshot")
        if created:
            db.executemany(
                """INSERT INTO kassalapp_history_rows
                (snapshot_id, ean, store_code, price_day, price_ore,
                 store_latest_checked_at, review_status)
                VALUES (?, ?, ?, ?, ?, ?, 'source_only')""",
                [(snap[0], *row) for row in rows],
            )
        else:
            count = db.execute(
                "SELECT COUNT(*) FROM kassalapp_history_rows WHERE snapshot_id=?",
                (snap[0],),
            ).fetchone()[0]
            if count != len(rows):
                raise ValueError("Existing snapshot has unexpected history row count")
    return len(payload["data"]), len(rows), created


def collect_history(eans: list[str], db_path: Path, *, days: int = 30):
    requested = validate_eans(eans, days)
    client = KassalappClient.from_environment()
    payload = client.bulk_price_history(requested, days=days, aggregation="min")
    return stage_history(payload, requested, db_path, days=days, aggregation="min")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ean", dest="eans", action="append", required=True,
                        help="Exact EAN, repeat for 1–100 products")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    try:
        count, rows, created = collect_history(args.eans, args.db, days=args.days)
    except (ValueError, KassalappError, sqlite3.Error, OSError) as exc:
        print(f"History staging failed: {exc}", file=sys.stderr)
        return 1
    print(f"Kassalapp: {count} products / {rows} daily source-only rows")
    print("Snapshot:", "new" if created else "already staged (idempotent)")
    print("PRIVATE SQLite only; no curated price/nutrition/ranking promotion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
