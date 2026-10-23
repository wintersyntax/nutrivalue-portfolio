"""TASK-032: evidence-driven Extra candidate equivalence, never guess Oslo ID.

The target region is Coop Øst, but Tjek catalog metadata has no regional
association; content equality does not independently prove region identity.
Capture candidate raw offers privately and compare their exact source facts.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from nutrivalue.providers.tjek_offline import CHAINS, MAX_FILE_BYTES, parse_snapshot, TjekSnapshotError
from nutrivalue.providers.tjek_weekly_collect import (
    PAGE_SIZE, CollectionError, _aware, _page, _private_save, _safe_id, _week,
)

DEALER = "80742m"
MAX_CANDIDATES = 9
MAX_PAGES = 5
MAX_GETS = 24


def eligible_catalogs(records: object, at: str) -> list[dict]:
    """Identify *current, offer-bearing* Extra flyers regardless of label.

    A true all_stores magazine with zero structured offers is not conflated
    with the weekly offer catalogues. No forced six-catalog assumption.
    """
    when = _aware(at)
    if not isinstance(records, list) or len(records) > PAGE_SIZE:
        raise CollectionError("invalid/unbounded Extra catalogue listing")
    seen = set()
    selected = []
    for obj in records:
        if not isinstance(obj, dict) or obj.get("dealer_id") != DEALER:
            raise CollectionError("invalid/foreign Extra catalogue")
        ident = _safe_id(obj.get("id"))
        if ident in seen:
            raise CollectionError("duplicate Extra catalogue ID")
        seen.add(ident)
        start, end = _aware(obj.get("run_from")), _aware(obj.get("run_till"))
        if end <= start:
            raise CollectionError("catalogue validity does not increase")
        count = obj.get("offer_count")
        if count is not None and (type(count) is not int or not 0 <= count <= 5000):
            raise CollectionError("invalid source offer count")
        published = obj.get("publish")
        if published and _aware(published) > when:
            continue
        if start <= when < end and count and obj.get("all_stores") is False:
            selected.append(obj)
    if not selected or len(selected) > MAX_CANDIDATES:
        raise CollectionError("expected 1..9 current bounded regional Extra catalogues")
    return sorted(selected, key=lambda obj: obj["id"])


def _fact(offer: dict) -> str:
    """Compare conservative, content-bearing source facts only.

    Exclude regional wrapper/IDs, source images and catalog page, but retain
    detailed source promotional conditions and offer validity. Different
    source descriptions intentionally do NOT silently deduplicate.
    """
    price = offer.get("pricing")
    if not isinstance(price, dict):
        raise CollectionError("missing Extra offer pricing object")
    canonical = {
        "heading": offer.get("heading"),
        "description": offer.get("description"),
        "pricing": {k: price.get(k) for k in ("price", "pre_price", "currency")},
        "quantity": offer.get("quantity"),
        "run_from": offer.get("run_from"),
        "run_till": offer.get("run_till"),
    }
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def resolve_offers(catalogs: list[dict], offers_by_id: dict[str, list[dict]]) -> tuple[dict, list[dict]]:
    """Return deduplicated common rows and counts; NOT proof of Øst identity."""
    facts_by_cat: dict[str, dict[str, dict]] = {}
    for cat in catalogs:
        cid = cat["id"]
        rows = offers_by_id[cid]
        facts: dict[str, dict] = {}
        for offer in rows:
            if offer.get("dealer_id") != DEALER or offer.get("catalog_id") not in (None, cid):
                raise CollectionError("invalid Extra offer linkage")
            key = _fact(offer)
            # Different source offer IDs can still repeat the same product
            # fact inside one booklet. Keep a stable representative instead of
            # treating repetition as a regional difference.
            if key not in facts or offer["id"] < facts[key]["id"]:
                facts[key] = offer
        facts_by_cat[cid] = facts
    shared = set.intersection(*(set(m) for m in facts_by_cat.values()))
    all_equal = all(set(m) == shared for m in facts_by_cat.values())
    representative = sorted(facts_by_cat)[0]  # deterministic provenance, NOT a regional label
    common_offers = [facts_by_cat[representative][k] for k in sorted(shared)]
    status = ("observed_content_equivalent_region_unproven" if all_equal
              else "common_subsets_only_region_unproven")
    comparison = {
        "status": status,
        "target_coop_region": "Coop Øst",
        "verified_tjek_catalog_for_ost": None,
        "region_identity_verified": False,
        "candidate_catalogs": len(catalogs),
        "common_offer_facts": len(shared),
        "same_contents": all_equal,
        "representative_catalog_for_source_rows": representative,
        "counts": [{
            "catalog_id": cid, "available_offer_facts": len(facts),
            "unique_to_this_candidate": len(set(facts) - shared),
        } for cid, facts in sorted(facts_by_cat.items())],
        "disclaimer": (
            "Content intersection is not proof that a particular Tjek ID "
            "serves Oslo/Coop Ost. No region-specific offer is promoted "
            "without official local-store/region mapping evidence."
        ),
    }
    return comparison, common_offers


def capture_and_resolve(*, metadata_path: Path, private_root: Path, as_of: str,
                        get_json, captured_at: datetime | None = None,
                        run_id: str | None = None) -> dict:
    """Owner-opt-in: <=24 scoped offer GETs, private immutable facts + manifest.

    Validity and metadata are read from the already archived Tjek catalog
    listing. No Tjek catalog-list requests or first-party website scraping.
    """
    when = _aware(as_of)
    root = Path(private_root).resolve(strict=True)
    source = Path(metadata_path)
    if source.is_symlink():
        raise CollectionError("catalog metadata must be a regular private file")
    try:
        full = source.resolve(strict=True)
        parts = full.relative_to(root).parts
    except (OSError, ValueError) as exc:
        raise CollectionError("catalog metadata outside private weekly archive") from exc
    if (len(parts) != 3 or parts[0] != _week(when)
            or not parts[1].startswith("diagnostic-")
            or parts[2] != f"{DEALER}.catalogs.json"
            or not full.is_file()):
        raise CollectionError("expected archived Extra catalog metadata for this week")
    if list((root / _week(when)).glob("extra-compare-*/manifest.json")):
        raise CollectionError("Extra comparison already attempted this week; preserve one-shot budget")
    try:
        raw = full.read_bytes()
        if len(raw) > 1_500_000:
            raise CollectionError("Extra metadata exceeds byte limit")
        records = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise CollectionError("invalid archived Extra metadata JSON") from exc
    candidates = eligible_catalogs(records, when.isoformat())
    stamp = _aware((captured_at or datetime.now(timezone.utc)).isoformat()).astimezone(timezone.utc)
    run_id = run_id or stamp.strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", run_id):
        raise CollectionError("unsafe comparison run ID")
    folder = root / _week(when) / f"extra-compare-{run_id}"
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    by_id: dict[str, list[dict]] = {}
    entries = []
    used = 0
    halted = None
    comparison = None
    shared_rows = []
    try:
        for cat in candidates:
            cid = cat["id"]
            rows = []
            terminal = False
            problem = None
            try:
                for page in range(MAX_PAGES):
                    if used >= MAX_GETS:
                        raise CollectionError("Extra HTTP budget exhausted")
                    used += 1
                    batch = _page(get_json("offers", {
                        "catalog_id": cid, "limit": PAGE_SIZE,
                        "offset": page * PAGE_SIZE,
                    }), catalog_id=cid, dealer=DEALER)
                    rows.extend(batch)
                    if len(batch) < PAGE_SIZE:
                        terminal = True
                        break
                if not terminal:
                    raise CollectionError("Extra pagination capped; complete candidate comparison blocked")
                envelope = json.dumps({"fetched_at": stamp.isoformat(),
                                       "entries": [{"catalog": cat, "offers": rows}]},
                                      sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8")
                if len(envelope) > MAX_FILE_BYTES:
                    raise CollectionError("Extra private snapshot exceeds bounded size")
                try:
                    validated = parse_snapshot(envelope, f"{cid}.json")
                except TjekSnapshotError as exc:
                    raise CollectionError(f"Extra candidate schema invalid: {exc}") from exc
                _private_save(folder, f"{cid}.json", envelope)
                by_id[cid] = rows
                entries.append({
                    "catalog_id": cid, "source_count": cat["offer_count"],
                    "available_offers": len(validated.offers),
                    "shortfall": max(0, cat["offer_count"] - len(validated.offers)),
                    "sha256": validated.sha256,
                    "status": "source_saved",
                })
            except CollectionError as exc:
                halted = str(exc)
                entries.append({"catalog_id": cid, "status": "error", "reason": halted,
                                "available_rows_before_error": len(rows)})
                break
        if not halted:
            comparison, shared_rows = resolve_offers(candidates, by_id)
            # Shared rows are not a verified LOCAL catalogue/price feed.
            # Do not materialize shared offers as a locally validated snapshot.
    finally:
        report = {
            "schema_version": 1, "week": _week(when), "as_of": when.isoformat(),
            "run_directory": folder.name, "http_requests": used, "max_gets": MAX_GETS,
            "scope": "Extra six-region candidates; offer source_only",
            "attempts": entries,
            "comparison": comparison,
            "source_catalog_counts_match": bool(not halted and all(
                entry["shortfall"] == 0 for entry in entries)),
            "shared_offers_in_memory": len(shared_rows),
            "status": "partial_or_unproven" if halted else comparison["status"],
            "halted_reason": halted,
            "metadata_sha256": hashlib.sha256(raw).hexdigest(),
            "no_verified_oslo_catalogue": True,
        }
        _private_save(folder, "manifest.json", json.dumps(
            report, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8"))
    return report
