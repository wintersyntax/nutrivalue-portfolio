#!/usr/bin/env python3
"""TASK-055: review the week's promotion queue in the terminal (no network).

For every open item it opens the catalogue page (macOS Preview), shows what
each model reported and asks for a decision, which is stored immediately and
append-only in the private offer database:

  ./scripts/ci-python scripts/review_promotions.py              # latest week
  ./scripts/ci-python scripts/review_promotions.py --week 2026-W43 --no-open

Keys: a = accept, r = reject, s = skip, q = quit (pages: c = checked, s, q).
A later decision on the same item replaces the earlier one in all views.
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.promotion_extraction import (  # noqa: E402
    ExtractionError, describe, page_image, record_review, review_items,
)

PRIVATE = ROOT / "data" / "private"


def _open(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)


def main(argv=None, *, ask=input, show=_open, out=print):
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--db", type=Path, default=PRIVATE / "offers.sqlite3")
    cli.add_argument("--week", help="YYYY-Www (default: latest extracted week)")
    cli.add_argument("--weekly-root", type=Path, default=PRIVATE / "tjek" / "weekly")
    cli.add_argument("--no-open", action="store_true", help="do not open page images")
    args = cli.parse_args(argv)
    db = args.db.resolve()
    if PRIVATE.resolve() not in db.parents:
        cli.error("--db must be inside data/private/")
    try:
        week, items = review_items(db, ROOT / "analytics", args.week)
    except (ExtractionError, sqlite3.Error) as exc:
        out(f"RED: {exc}")
        return 1
    out(f"{week}: {len(items)} open item(s) to review")
    done = 0
    for number, item in enumerate(items, 1):
        page = item["page_info"]
        out("")
        out(f"[{number}/{len(items)}] {item['chain_name']} - catalogue {item['catalog_id']} "
            f"page {item['page']} (valid {page['valid_from'][:10]} to {page['valid_to'][:10]})")
        for model, parsed in item["answers"].items():
            if parsed is None:
                out(f"  {model}: no valid answer")
            else:
                found = "; ".join(f"{describe(p)}: {p['scope']}" for p in parsed["promotions"])
                out(f"  {model}: {parsed['page_class']} - {found or 'no promotions'}")
        image = page_image(args.weekly_root, page["run_id"], item["catalog_id"], item["page"],
                           page["image_sha256"])
        if image is None:
            out("  (page image no longer available locally)")
        elif not args.no_open:
            show(image)
        if item["item_no"] is None:
            out("  Page: no valid answer from both models. Check the page yourself.")
            keys = {"c": "page_checked"}
            prompt = "  [c] checked / [s] skip / [q] quit: "
        else:
            promo = item["promotion"]
            out(f"  TO DECIDE: {describe(promo)} - {promo['scope']} "
                f"({item['reason']}; OCR {promo['ocr_status'] or 'n/a'})")
            keys = {"a": "accept", "r": "reject"}
            prompt = "  [a] accept / [r] reject / [s] skip / [q] quit: "
        while True:
            choice = (ask(prompt) or "").strip().lower()
            if choice in keys or choice in ("s", "q"):
                break
        if choice == "q":
            break
        if choice == "s":
            continue
        note = ask("  note (optional, Enter to skip): ") if choice == "r" else ""
        record_review(db, ROOT / "analytics", item, keys[choice], note)
        done += 1
    out(f"\nSaved {done} decision(s). Open items left: {len(items) - done}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
