"""TASK-058: review decisions as create-only objects, replayed into the database.

The hosted review page never writes the SQLite database. Each decision is one
small JSON object under ``reviews/`` in the private bucket, written with
``if_generation_match=0`` (it can never be replaced). The weekly job, and the
review page for its own working copy, replay every decision exactly once into
``promo_reviews``; ``promo_review_log`` remembers which ones were applied and
which were invalid, so replaying the same objects again changes nothing.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from nutrivalue.promotion_extraction import _connect, insert_review

PREFIX = "reviews/"
DECISIONS = ("accept", "reject", "page_checked")
MAX_NOTE = 500
DECISION_ID = re.compile(r"[0-9a-f]{32}")
FIELDS = {"decision_id", "extraction_id", "catalog_id", "page", "item_no", "decision", "note",
          "reviewed_at", "reviewer"}


class ReviewLogError(ValueError):
    """A decision object is malformed."""


def make(item: dict, decision: str, note: str | None, reviewer: str,
         now: datetime | None = None) -> dict:
    """A new, validated decision with a random id and the server's time."""
    at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return validate({"decision_id": uuid.uuid4().hex, "extraction_id": item["extraction_id"],
                     "catalog_id": item["catalog_id"], "page": item["page"],
                     "item_no": item["item_no"], "decision": decision,
                     "note": (note or "").strip()[:MAX_NOTE] or None,
                     "reviewed_at": at.isoformat(timespec="microseconds"),
                     "reviewer": reviewer})


def validate(raw) -> dict:
    """The decision as stored, or ReviewLogError."""
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        raise ReviewLogError("decision fields are wrong")
    if not isinstance(raw["decision_id"], str) or not DECISION_ID.fullmatch(raw["decision_id"]):
        raise ReviewLogError("bad decision_id")
    for key in ("extraction_id", "catalog_id", "reviewer"):
        if not isinstance(raw[key], str) or not raw[key] or len(raw[key]) > 300:
            raise ReviewLogError(f"bad {key}")
    if type(raw["page"]) is not int or raw["page"] < 1:
        raise ReviewLogError("bad page")
    if raw["item_no"] is not None and (type(raw["item_no"]) is not int or raw["item_no"] < 0):
        raise ReviewLogError("bad item_no")
    expected = ("page_checked",) if raw["item_no"] is None else ("accept", "reject")
    if raw["decision"] not in expected:
        raise ReviewLogError(f"decision must be one of {', '.join(expected)}")
    if raw["note"] is not None and (not isinstance(raw["note"], str)
                                    or len(raw["note"]) > MAX_NOTE):
        raise ReviewLogError("bad note")
    try:
        at = datetime.fromisoformat(raw["reviewed_at"])
    except (TypeError, ValueError):
        raise ReviewLogError("bad reviewed_at") from None
    if at.tzinfo is None:
        raise ReviewLogError("reviewed_at needs a timezone")
    return dict(raw)


def object_name(decision: dict) -> str:
    stamp = decision["reviewed_at"][:26].replace(":", "").replace("-", "")
    return f"{PREFIX}{decision['reviewed_at'][:10]}/{stamp}-{decision['decision_id']}.json"


def encode(decision: dict) -> bytes:
    return json.dumps(decision, ensure_ascii=False, sort_keys=True).encode()


def target_exists(db: sqlite3.Connection, decision: dict) -> bool:
    """The page or the item the decision is about exists in this database."""
    if decision["item_no"] is None:
        row = db.execute("""SELECT 1 FROM promo_pages WHERE extraction_id=? AND catalog_id=?
                            AND page=?""", (decision["extraction_id"], decision["catalog_id"],
                                            decision["page"])).fetchone()
    else:
        row = db.execute("""SELECT 1 FROM page_promotions WHERE extraction_id=? AND catalog_id=?
                            AND page=? AND item_no=?""",
                         (decision["extraction_id"], decision["catalog_id"], decision["page"],
                          decision["item_no"])).fetchone()
    return row is not None


def replay(db_path: Path, schema_dir: Path, raw_decisions) -> dict:
    """Apply decisions not seen before, oldest first. Returns counts."""
    counts = {"applied": 0, "already": 0, "invalid": 0, "malformed": 0}
    decisions = []
    for raw in raw_decisions:
        try:
            decisions.append(validate(raw))
        except ReviewLogError:
            counts["malformed"] += 1
    decisions.sort(key=lambda d: (d["reviewed_at"], d["decision_id"]))
    with _connect(db_path, schema_dir) as db:
        known = {row[0] for row in db.execute("SELECT decision_id FROM promo_review_log")}
        now = datetime.now(timezone.utc).isoformat()
        for decision in decisions:
            if decision["decision_id"] in known:
                counts["already"] += 1
                continue
            known.add(decision["decision_id"])
            if not target_exists(db, decision):
                db.execute("""INSERT INTO promo_review_log (decision_id,review_id,reviewer,
                              outcome,error,replayed_at) VALUES (?,NULL,?,'invalid',?,?)""",
                           (decision["decision_id"], decision["reviewer"],
                            "unknown page or item", now))
                counts["invalid"] += 1
                continue
            review_id = insert_review(db, decision, decision["decision"], decision["note"],
                                      decision["reviewed_at"])
            db.execute("""INSERT INTO promo_review_log (decision_id,review_id,reviewer,outcome,
                          error,replayed_at) VALUES (?,?,?,'applied',NULL,?)""",
                       (decision["decision_id"], review_id, decision["reviewer"], now))
            counts["applied"] += 1
    return counts


def load_dir(directory: Path) -> list:
    """Every decision object saved under ``directory`` (unreadable ones as None)."""
    out = []
    for path in sorted(Path(directory).rglob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            out.append(None)
    return out
