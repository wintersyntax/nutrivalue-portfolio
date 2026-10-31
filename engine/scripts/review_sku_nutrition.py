#!/usr/bin/env python3
"""Offline TASK-002 review of private Kassalapp staging vs reviewed manufacturer data.

Default: read-only preview. --apply explicitly appends immutable review rows
ONLY to a private SQLite table. Never calls an API, ranks a price, or writes
products, nutrition_profiles, or price_observations.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.sku_nutrition_review import parse_evidence, review_private_db  # noqa: E402

DEFAULT_DB = ROOT / "data/private/nutrivalue.sqlite3"
DEFAULT_EVIDENCE = ROOT / "data/private/sku-nutrition-evidence.json"


def load_evidence(path: Path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or set(doc) != {"evidence"}:
        raise ValueError("Expected JSON object with exactly one 'evidence' array")
    items = doc["evidence"]
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise ValueError("Expected between 1 and 50 manufacturer evidence items")
    parsed = [parse_evidence(item) for item in items]
    if len({x.ean for x in parsed}) != len(parsed):
        raise ValueError("Duplicate manufacturer EAN in one evidence file")
    return parsed


def run(db_path: Path, evidence_path: Path, *, apply: bool = False):
    if not db_path.is_file():
        raise ValueError("Private SQLite DB does not exist; refusing to create it")
    evidence = load_evidence(evidence_path)
    # URI mode=ro guarantees that review preview cannot mutate the local DB.
    uri = "file:" + quote(str(db_path.resolve()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        reviews = review_private_db(db, evidence)
    for item in reviews:
        print(f"{item.evidence.ean} | identity={item.identity_status} | "
              f"nutrition={item.nutrition_status} | overall={item.overall_status} | "
              f"price={item.price_status} | candidates={item.candidate_count}")
        if item.reasons:
            print("  review codes: " + ", ".join(item.reasons))
    if not apply:
        print("READ-ONLY PREVIEW: no changes; --apply writes PRIVATE review rows only.")
        return len(reviews), 0
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inserted = 0
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript((ROOT / "analytics/schema.sql").read_text(encoding="utf-8"))
        for r in reviews:
            e = r.evidence
            cursor = db.execute(
                """INSERT INTO sku_nutrition_reviews
                (ean, review_sha256, evidence_sha256, brand, canonical_name,
                 net_weight_g, protein_per_100g, kcal_per_100g, carbs_per_100g,
                 nutrition_basis, source_url, evidence_checked_on,
                 reviewer_confirmed, reviewer_note, identity_status,
                 nutrition_status, overall_status, price_status, candidate_count,
                 matched_staging_ids_json, reasons_json, raw_evidence_json, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'per_100g_as_sold', ?, ?, ?, ?,
                        ?, ?, ?, 'source_only', ?, ?, ?, ?, ?)
                ON CONFLICT(review_sha256) DO NOTHING""",
                (e.ean, r.review_sha256, e.evidence_sha256, e.brand, e.name,
                 float(e.net_weight_g), float(e.protein_per_100g),
                 float(e.kcal_per_100g), float(e.carbs_per_100g),
                 e.source_url, e.checked_on, int(e.reviewer_confirmed),
                 e.reviewer_note, r.identity_status, r.nutrition_status,
                 r.overall_status, r.candidate_count, r.staging_ids_json,
                 json.dumps(r.reasons), e.raw_evidence_json, stamp),
            )
            inserted += cursor.rowcount
    print(f"PRIVATE REVIEW ONLY: {inserted} new rows, {len(reviews)-inserted} identical replays.")
    print("Curated products, nutrition_profiles and price_observations untouched.")
    return len(reviews), inserted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--apply", action="store_true",
                        help="Append PRIVATE immutable review snapshots (not curated rows)")
    args = parser.parse_args()
    try:
        run(args.db, args.evidence, apply=args.apply)
    except (ValueError, json.JSONDecodeError, sqlite3.Error, OSError) as exc:
        print(f"SKU nutrition review failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
