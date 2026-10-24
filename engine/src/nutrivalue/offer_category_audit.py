"""TASK-041: aggregate-only audit of *saved* private Tjek offer categories.

This module NEVER fetches data or writes to SQLite. Output contains no source
headings, descriptions, offer IDs, exact prices or potentially personal paths.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from nutrivalue.offer_categories import CATEGORIES, STATES, TAXONOMY_VERSION, classify_offer
from nutrivalue.providers.tjek_offline import CHAINS, MAX_FILE_BYTES, TjekSnapshotError, parse_snapshot
from nutrivalue.providers.tjek_offer_preview import (
    MAX_ROWS, RELIABLE_PRINT_STATE, _date_window, _time,
)


class OfferCategoryAuditError(ValueError):
    """Invalid input or incomplete unsafe private staging evidence."""


# Intentionally select only existing source-only fields; no snapshot BLOB,
# no new Tjek call, no inferred EAN, and no 'category_ids' column.
SQL = """
SELECT o.dealer_id, c.chain_name, o.catalog_id, o.offer_id,
       o.snapshot_sha256, o.heading, o.description,
       o.review_state, o.price_ore, o.currency,
       o.quantity_diagnostic, o.unit_price_ore, o.unit_price_basis,
       o.unit_price_confidence, o.member_condition,
       o.multibuy_condition, o.deposit_condition,
       o.run_from, o.run_till, c.run_from AS catalog_from,
       c.run_till AS catalog_till, s.fetched_at
FROM tjek_offers o
JOIN tjek_catalogs c
  ON o.catalog_id=c.catalog_id AND o.snapshot_sha256=c.snapshot_sha256
JOIN tjek_snapshots s ON o.snapshot_sha256=s.sha256
ORDER BY o.dealer_id, o.catalog_id, o.offer_id, s.fetched_at DESC
LIMIT ?
"""


def _rate_eligible(row) -> bool:
    return (row["unit_price_confidence"] == RELIABLE_PRINT_STATE
            and row["quantity_diagnostic"] == "exact_quantity"
            and row["unit_price_basis"] == "kg"
            and type(row["unit_price_ore"]) is int
            and row["unit_price_ore"] > 0
            and row["member_condition"] == 0
            and row["multibuy_condition"] == 0
            and row["deposit_condition"] == 0)


def audit_saved_offers(db_path: Path, *, as_of: str) -> dict:
    """Count latest distinct staged offer identities, no raw records in result."""
    try:
        now = _time(as_of)
    except (ValueError, TypeError) as exc:
        raise OfferCategoryAuditError("as_of requires an aware ISO-8601 timestamp") from exc
    if not isinstance(db_path, Path) or not db_path.is_file():
        raise OfferCategoryAuditError("private Tjek staging database is unavailable")
    try:
        with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            raw_count = db.execute("SELECT COUNT(*) FROM tjek_offers").fetchone()[0]
            if type(raw_count) is not int or raw_count > MAX_ROWS:
                raise OfferCategoryAuditError(
                    "saved private offer inventory exceeds the offline audit cap")
            rows = db.execute(SQL, (MAX_ROWS + 1,)).fetchall()
    except sqlite3.Error as exc:
        raise OfferCategoryAuditError("private Tjek schema or input is unavailable") from exc
    if len(rows) != raw_count:
        # Never report good coverage after an inner JOIN silently drops
        # a malformed orphan offer/catalog/snapshot, even if FK was disabled.
        raise OfferCategoryAuditError("source staging has unjoined offers")


    return _aggregate(rows, now)


def _aggregate(rows, now) -> dict:
    """Shared SQLite/validated-source memory view; no source fields returned."""
    latest = {}
    for row in rows:
        if row["dealer_id"] not in CHAINS or CHAINS[row["dealer_id"]] != row["chain_name"]:
            raise OfferCategoryAuditError("unrecognized Tjek dealer/chain mapping")
        if row["review_state"] != "source_only":
            raise OfferCategoryAuditError("unexpected promoted source price")
        key = (row["dealer_id"], row["catalog_id"], row["offer_id"])
        try:
            fetched = _time(row["fetched_at"])
        except (TypeError, ValueError) as exc:
            raise OfferCategoryAuditError("invalid source snapshot timestamp") from exc
        previous = latest.get(key)
        if previous is None or (fetched, row["snapshot_sha256"]) > (
                _time(previous["fetched_at"]), previous["snapshot_sha256"]):
            latest[key] = row

    statuses = Counter()
    categories = Counter()
    tags = Counter()
    tag_states = Counter()
    offer_kinds = Counter()
    tag_resolved = 0
    multi_tag = 0
    forms = Counter()
    reasons = Counter()
    windows = Counter()
    chains = {}
    food_key_matches = 0
    possible_kg = 0
    conditioned = 0
    active = 0
    for row in latest.values():
        try:
            window = _date_window(row, now)
            result = classify_offer(row["heading"], row["description"])
        except (ValueError, TypeError) as exc:
            raise OfferCategoryAuditError("invalid source text or validity") from exc
        flags = (row["member_condition"], row["multibuy_condition"],
                 row["deposit_condition"])
        if any(type(v) is not int or v not in (0, 1) for v in flags):
            raise OfferCategoryAuditError("invalid source pricing conditions")
        if row["price_ore"] is not None and (
                type(row["price_ore"]) is not int or row["price_ore"] <= 0
                or row["currency"] != "NOK"):
            raise OfferCategoryAuditError("invalid advertised price/currency")

        statuses[result.status] += 1
        categories[result.food_category or "unresolved"] += 1
        tags.update(result.category_tags)
        tag_states[result.tag_status] += 1
        offer_kinds[result.offer_kind] += 1
        tag_resolved += int(result.tag_status == "complete" and bool(result.category_tags))
        multi_tag += int(len(result.category_tags) > 1)
        forms[result.form] += 1
        reasons[result.reason] += 1
        windows[window] += 1
        if result.food_key:
            food_key_matches += 1
        if any(flags):
            conditioned += 1
        if window == "current":
            active += 1
        if result.food_analysis_eligible and _rate_eligible(row):
            possible_kg += 1

        chain = row["chain_name"]
        if chain not in chains:
            chains[chain] = Counter()
        chains[chain]["all"] += 1
        chains[chain][result.status] += 1
        chains[chain]["current"] += int(window == "current")

    count = len(latest)
    if set(statuses) - STATES or sum(statuses.values()) != count:
        raise OfferCategoryAuditError("inconsistent category state inventory")
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "as_of": now.isoformat(),
        "origin": "tjek_private_saved_source_only",
        "snapshot_rows": len(rows),
        "distinct_observed_offers": count,
        "active_offers": active,
        "status": {s: statuses[s] for s in sorted(STATES)},
        "by_category": dict(sorted(categories.items())),
        "by_tag": dict(sorted(tags.items())),
        "by_tag_status": dict(sorted(tag_states.items())),
        "by_offer_kind": dict(sorted(offer_kinds.items())),
        "tag_resolved_offers": tag_resolved,
        "multi_tag_offers": multi_tag,
        "by_chain": {k: dict(sorted(v.items())) for k, v in sorted(chains.items())},
        "by_validity": dict(sorted(windows.items())),
        "by_form": dict(sorted(forms.items())),
        "by_reason": dict(sorted(reasons.items())),
        "potential_food_key_types": food_key_matches,
        "conditionally_priced": conditioned,
        "unconditioned_source_consistent_kg_rate": possible_kg,
        "notice": (
            "Offline observed staging only; taxonomy may miss categories; "
            "no current-shop/stock/EAN/nutrition verification and no nationwide "
            "completeness claim. No raw offer titles, IDs or prices are emitted."
        ),
        "network_requests": 0,
        "database_writes": 0,
    }



def audit_saved_archives(paths: list[Path], *, as_of: str) -> dict:
    """Parse saved one-chain Tjek snapshot files directly; never touch SQLite.

    No network requests, filesystem writes or source-title/offer-ID output.
    All source records pass the existing TASK-023 parser before auditing.
    """
    try:
        now = _time(as_of)
    except (ValueError, TypeError) as exc:
        raise OfferCategoryAuditError("as_of requires an aware ISO-8601 timestamp") from exc
    if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= len(CHAINS):
        raise OfferCategoryAuditError("supply one to nine saved chain snapshots")
    rows = []
    coverage = {}
    dealers_seen = set()
    for item in paths:
        if not isinstance(item, Path):
            raise OfferCategoryAuditError("saved source path must be a Path")
        try:
            with item.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            raise OfferCategoryAuditError("saved source archive missing/unreadable") from exc
        try:
            snap = parse_snapshot(raw, item.name)
        except (TjekSnapshotError, ValueError) as exc:
            # No upstream error text may disclose source-provided content.
            raise OfferCategoryAuditError("saved source archive failed offline validation") from exc
        dealer_ids = {cat[2] for cat in snap.catalogs}
        if len(dealer_ids) != 1:
            raise OfferCategoryAuditError("each saved source archive must contain one chain")
        dealer = next(iter(dealer_ids))
        if dealer in dealers_seen:
            raise OfferCategoryAuditError("multiple inputs for the same chain")
        dealers_seen.add(dealer)
        cats = {cat[1]: cat for cat in snap.catalogs}
        per_cat = Counter(offer[1] for offer in snap.offers)
        claimed = [cat[8] for cat in snap.catalogs]
        if any(v is None for v in claimed):
            status = "unknown_source_counts"
        elif any(per_cat[cat[1]] != cat[8] for cat in snap.catalogs):
            status = "source_count_mismatch"
        else:
            status = "source_declared_match"
        chain = CHAINS[dealer]
        coverage[chain] = {
            "catalogs": len(cats),
            "observed": len(snap.offers),
            "declared": sum(claimed) if all(v is not None for v in claimed) else None,
            "source_status": status,
        }
        for offer in snap.offers:
            if len(rows) >= MAX_ROWS:
                raise OfferCategoryAuditError(
                    "saved private offer inventory exceeds the offline audit cap")
            cat = cats.get(offer[1])
            if cat is None or offer[3] != dealer:
                raise OfferCategoryAuditError("saved source offer has inconsistent catalogue")
            rows.append({
                "snapshot_sha256": snap.sha256,
                "catalog_id": offer[1],
                "offer_id": offer[2],
                "dealer_id": dealer,
                "chain_name": chain,
                "heading": offer[4],
                "description": offer[5],
                "price_ore": offer[6],
                "currency": offer[8],
                "quantity_diagnostic": offer[12],
                "unit_price_ore": offer[13],
                "unit_price_basis": offer[14],
                "unit_price_confidence": offer[17],
                "member_condition": offer[18],
                "multibuy_condition": offer[19],
                "deposit_condition": offer[20],
                "run_from": offer[21],
                "run_till": offer[22],
                "review_state": offer[26],
                "catalog_from": cat[5],
                "catalog_till": cat[6],
                "fetched_at": snap.fetched_at,
            })
    report = _aggregate(rows, now)
    report["archive_inputs"] = len(paths)
    report["missing_selected_chains"] = sorted(set(CHAINS.values()) - set(coverage))
    report["by_chain_source_completeness"] = dict(sorted(coverage.items()))
    return report
