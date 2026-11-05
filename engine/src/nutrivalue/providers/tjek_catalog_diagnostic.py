"""TASK-032: fixed-scope, metadata-only diagnosis for four unmatched Tjek dealers.

Only catalog headers are requested, at most one GET per named dealer.
No offer fetches, SQLite writes, existing archive edits or cloud requests.
Owner-private immutable per-dealer JSON captures preserve evidence for later
offline analysis; stdout reports counts/reasons, not source labels or IDs.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from nutrivalue.providers.tjek_offline import CHAINS
from nutrivalue.providers.tjek_weekly_collect import (
    PAGE_SIZE, WEEKLY, CollectionError, _aware, _private_save, _safe_id, _week,
)

MISSING_DEALERS = ("f5d5lm", "80742m", "b3e8Fm", "2686gD")
REASONS = (
    "published_later", "future", "expired", "active_no_weekly_label",
    "active_missing_offer_count", "active_zero_offers",
    "eligible_weekly", "active_other_unclassified",
)


def catalog_reasons(body: object, dealer: str, as_of: str) -> dict:
    """Classify the *same predicates* as the TASK-032 weekly collector.

    Return only counts; do not print private catalog IDs or labels. This is
    source listing evidence, not proof that a dealer has zero weekly offers.
    """
    when = _aware(as_of)
    if dealer not in MISSING_DEALERS:
        raise CollectionError("diagnostic target outside four selected dealers")
    if not isinstance(body, list) or len(body) > PAGE_SIZE:
        raise CollectionError("unbounded or invalid catalog list")
    counts: Counter[str] = Counter()
    for item in body:
        if not isinstance(item, dict) or item.get("dealer_id") != dealer:
            raise CollectionError("foreign or malformed catalog in response")
        _safe_id(item.get("id"))
        label = item.get("label")
        if label is not None and not isinstance(label, str):
            raise CollectionError("invalid catalog label")
        count = item.get("offer_count")
        if count is not None and (type(count) is not int or not 0 <= count <= 5000):
            raise CollectionError("invalid declared offer count")
        start, end = _aware(item.get("run_from")), _aware(item.get("run_till"))
        if end <= start:
            raise CollectionError("invalid catalog date window")
        published = item.get("publish")
        if published is not None and _aware(published) > when:
            reason = "published_later"
        elif start > when:
            reason = "future"
        elif end <= when:
            reason = "expired"
        elif not label or not WEEKLY.search(label):
            reason = "active_no_weekly_label"
        elif count is None:
            reason = "active_missing_offer_count"
        elif count == 0:
            reason = "active_zero_offers"
        else:
            reason = "eligible_weekly"
        counts[reason] += 1
    return {
        "dealer_id": dealer, "chain": CHAINS[dealer],
        "returned_catalogs": len(body),
        "response_limit_reached": len(body) == PAGE_SIZE,
        "reasons": {key: counts[key] for key in REASONS},
        "weekly_filter_matches": counts["eligible_weekly"],
        "possible_active_other_labels": counts["active_no_weekly_label"],
        "source_only": True,
    }


def diagnose_missing_catalogs(*, as_of: str, out_dir: Path, get_json,
                              run_id: str | None = None,
                              captured_at: datetime | None = None) -> dict:
    """One private diagnostic attempt, maximum four serial catalog GETs.

    get_json(endpoint, params) can be a deterministic offline fake.
    Transport errors stop further GETs, retain prior metadata and mark
    remaining chains not_attempted. Never amend a historic capture manifest.
    """
    when = _aware(as_of)
    stamp = _aware((captured_at or datetime.now(timezone.utc)).isoformat())
    stamp = stamp.astimezone(timezone.utc)
    run_id = run_id or stamp.strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", run_id):
        raise CollectionError("unsafe catalog diagnostic run ID")
    weekdir = Path(out_dir) / _week(when)
    if list(weekdir.glob("diagnostic-*/manifest.json")):
        raise CollectionError("diagnostic already attempted this week; no repeat by default")
    folder = weekdir / f"diagnostic-{run_id}"
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    entries: list[dict] = []
    halt: str | None = None
    used = 0
    try:
        for dealer in MISSING_DEALERS:
            used += 1
            try:
                body = get_json("catalogs", {"dealer_ids": dealer, "limit": PAGE_SIZE})
                summary = catalog_reasons(body, dealer, when.isoformat())
                # Preserve exactly the returned JSON *fields* in private storage.
                # Not claimed byte-identical to original HTTP response body.
                raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
                if len(raw) > 1_500_000:
                    raise CollectionError("catalog metadata exceeds 1.5MB")
                _private_save(folder, f"{dealer}.catalogs.json", raw)
                entries.append({
                    **summary,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "private_file": f"{dealer}.catalogs.json",
                    "status": "diagnosed",
                })
            except CollectionError as exc:
                halt = str(exc)
                entries.append({
                    "dealer_id": dealer, "chain": CHAINS[dealer],
                    "status": "error", "reason": halt,
                })
                break
    finally:
        for dealer in MISSING_DEALERS[len(entries):]:
            entries.append({
                "dealer_id": dealer, "chain": CHAINS[dealer],
                "status": "not_attempted",
            })
        report = {
            "schema_version": 1, "as_of": when.isoformat(),
            "fetched_at": stamp.isoformat(), "week": _week(when),
            "run_directory": folder.name, "scope": "metadata-only, four selected Tjek dealers",
            "http_requests": used, "max_requests": len(MISSING_DEALERS),
            "offers_fetched": 0, "halted_reason": halt, "chains": entries,
            "disclaimer": "No matching weekly label does not prove no active store offers.",
        }
        _private_save(folder, "manifest.json", json.dumps(
            report, ensure_ascii=False, sort_keys=True,
            indent=2).encode("utf-8"))
    return report
