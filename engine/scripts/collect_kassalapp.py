#!/usr/bin/env python3
"""Fetch one bounded Kassalapp search into a private SQLite review queue.

No price or nutrition record is promoted to verified product tables by this
script. Requires an API key in the environment; never print or commit it.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from a clean checkout without pip installing this package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.kassalapp import (  # noqa: E402
    KassalappClient, KassalappError, stage_product,
)


def collect(search: str, db_path: Path, size: int = 20) -> tuple[int, int]:
    # Fail without writing if credentials are absent or API call fails.
    client = KassalappClient.from_environment()
    payload = client.search_products(search, size=size)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = [(stage_product(item), item) for item in payload["data"]]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript((ROOT / "analytics/schema.sql").read_text(encoding="utf-8"))
        for row, raw in records:
            db.execute(
                """INSERT INTO kassalapp_staging (
                    provider_product_id, ean, product_name, brand, store_code,
                    price_nok, reported_weight_g, protein_g_unverified_basis,
                    fetched_at, price_checked_at, review_status, review_notes,
                    source_ref, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
                (row.provider_product_id, row.ean, row.name, row.brand,
                 row.store_code, row.price_nok, row.weight_g,
                 row.protein_g_unverified_basis, stamp,
                 row.review_status, row.review_notes, row.source_ref,
                 json.dumps(raw, ensure_ascii=False, sort_keys=True)),
            )
    return len(records), sum(row.review_status == "needs_review" for row, _ in records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search", required=True, help="Product search, e.g. kyllingfilet")
    parser.add_argument("--size", type=int, default=20, help="1–100 products, default 20")
    parser.add_argument(
        "--db", type=Path,
        default=ROOT / "data/private/nutrivalue.sqlite3",
        help="Local private DB path (never commit provider snapshots)",
    )
    args = parser.parse_args()
    try:
        count, needs_review = collect(args.search, args.db, args.size)
    except (ValueError, KassalappError, sqlite3.Error) as exc:
        print(f"Collection not completed: {exc}", file=sys.stderr)
        return 1
    print(f"Fetched {count} source products into PRIVATE staging ({needs_review} review candidates).")
    print("No records were marked VERIFIED or imported into live ranking.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
