"""Offline-only completeness/freshness gate for saved Tjek weekly inputs.

TASK-030 prepares TASK-024 without making any HTTP or cloud calls.
Candidate coverage means *provided source records* pass local checks, not
complete Tjek coverage, independent price truth or permission to fetch.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Sequence

from nutrivalue.providers.tjek_offline import (
    CHAINS,
    MAX_FILE_BYTES,
    TjekSnapshotError,
    parse_snapshot,
)


class WeeklyAuditError(ValueError):
    """Invalid saved weekly input or explicit audit timestamp."""


def _when(value: str) -> datetime:
    if not isinstance(value, str):
        raise WeeklyAuditError("as_of must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WeeklyAuditError("as_of must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WeeklyAuditError("as_of must include a timezone")
    return parsed


def _within(start: str, end: str, at: datetime) -> bool:
    return _when(start) <= at < _when(end)


def audit_saved_week(inputs: Sequence[Path], *, as_of: str) -> dict:
    """Read saved bytes once, validate with TASK-023 and report coverage only.

    No database, network, filesystem writes or provider refresh. Returning
    candidate_complete does NOT certify live collection or local prices.
    """
    at = _when(as_of)
    if not inputs or len(inputs) > len(CHAINS):
        raise WeeklyAuditError("supply 1..9 saved chain snapshots")
    per_dealer = {}

    for path in inputs:
        path = Path(path)
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            raise WeeklyAuditError("unreadable saved source snapshot") from exc
        try:
            snapshot = parse_snapshot(raw, path.name)
        except TjekSnapshotError as exc:
            raise WeeklyAuditError(f"invalid saved snapshot: {exc}") from exc
        dealers = {catalog[2] for catalog in snapshot.catalogs}
        if len(dealers) != 1:
            raise WeeklyAuditError("one saved input must contain one chain only")
        dealer = dealers.pop()
        if dealer in per_dealer:
            raise WeeklyAuditError("duplicate chain input; cannot prove weekly coverage")

        actual = Counter(offer[1] for offer in snapshot.offers)
        declared = [catalog[8] for catalog in snapshot.catalogs]
        if any(value is not None and actual[catalog[1]] != value
               for catalog, value in zip(snapshot.catalogs, declared)):
            status = "count_mismatch"
        elif any(value is None for value in declared):
            status = "unknown_source_counts"
        elif _when(snapshot.fetched_at) > at:
            status = "fetched_after_as_of"
        else:
            active = {
                catalog[1] for catalog in snapshot.catalogs
                if _within(catalog[5], catalog[6], at)
            }
            if not active:
                status = "catalog_out_of_window"
            elif not any(
                offer[1] in active and _within(offer[21], offer[22], at)
                for offer in snapshot.offers
            ):
                status = "no_current_offers"
            else:
                status = "candidate_current"

        per_dealer[dealer] = {
            "dealer_id": dealer,
            "chain": CHAINS[dealer],
            "status": status,
            "snapshot_sha256": snapshot.sha256,
            "catalogs": len(snapshot.catalogs),
            "offers": len(snapshot.offers),
            "declared_counts_match": all(value is not None for value in declared)
                and all(actual[catalog[1]] == value
                        for catalog, value in zip(snapshot.catalogs, declared)),
            "fetched_at": snapshot.fetched_at,
        }

    chains = [
        per_dealer.get(dealer, {
            "dealer_id": dealer,
            "chain": name,
            "status": "missing",
            "snapshot_sha256": None,
            "catalogs": None,
            "offers": None,
            "declared_counts_match": False,
            "fetched_at": None,
        })
        for dealer, name in CHAINS.items()
    ]
    good = sum(item["status"] == "candidate_current" for item in chains)
    return {
        "schema_version": 1,
        "as_of": at.isoformat(),
        "scope": "owner-selected-nine-chains; source_only",
        "batch_status": "candidate_complete" if good == len(CHAINS)
                        else "partial_or_unproven",
        "candidate_chains": good,
        "required_chains": len(CHAINS),
        "chains": chains,
        "network_requests": 0,
        "mutations": 0,
        "disclaimer": (
            "Saved-source coverage check only; not independently verified "
            "store pricing, full live Tjek availability or API authorization."
        ),
    }
