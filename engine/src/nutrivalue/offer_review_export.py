"""TASK-041: owner-authorized minimal review export from saved private Tjek.

Use only existing per-chain private source-only archives. No HTTP; never
check source titles/offer IDs/prices into NutriValue's code repository.
Owner may transfer the minimal output to a SEPARATE, PRIVATE GitHub repo.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from nutrivalue.offer_categories import classify_offer
from nutrivalue.providers.tjek_offline import (
    CHAINS, MAX_FILE_BYTES, TjekSnapshotError, parse_snapshot,
)

MAX_EXPORT_BYTES = 4_000_000


class ExportError(ValueError):
    """Safe-to-print exporter error with no raw source text or private path."""


def assemble_review_export(archive_dir: Path) -> tuple[bytes, dict]:
    """One JSONL file; only chain, heading, description and current rule guess.

    No original source IDs, GTIN, structured prices, location, raw payload,
    supplier metadata, source hash or timestamps leave this parser.
    Description text is reproduced verbatim and may itself contain prices.
    """
    if not isinstance(archive_dir, Path) or not archive_dir.is_dir():
        raise ExportError("saved Tjek archive directory is unavailable")
    filenames = [archive_dir / f"{dealer}.json" for dealer in CHAINS]
    if not all(p.is_file() and not p.is_symlink() for p in filenames):
        raise ExportError("nine selected dealer archives are required")
    records = []
    by_chain = Counter()
    by_status = Counter()
    declared = 0
    seen_snapshot_fingerprints = set()
    for dealer, path in zip(CHAINS, filenames):
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_FILE_BYTES + 1)
            snap = parse_snapshot(raw, path.name)
        except (OSError, TjekSnapshotError, ValueError) as exc:
            raise ExportError("existing private dealer snapshot failed validation") from exc
        if snap.sha256 in seen_snapshot_fingerprints:
            raise ExportError("duplicate saved source snapshot")
        seen_snapshot_fingerprints.add(snap.sha256)
        if {cat[2] for cat in snap.catalogs} != {dealer}:
            raise ExportError("archived dealer identity mismatch")
        if any(cat[8] is None for cat in snap.catalogs):
            raise ExportError("source declared offer counts unavailable")
        declared += sum(cat[8] for cat in snap.catalogs)
        for row in snap.offers:
            if row[3] != dealer:
                raise ExportError("source offer dealer mismatch")
            try:
                decision = classify_offer(row[4], row[5])
            except (ValueError, TypeError) as exc:
                raise ExportError("invalid review text in saved source") from exc
            records.append({
                "n": len(records) + 1,
                "chain": CHAINS[dealer],
                "heading": row[4],
                "description": row[5],
                "rule_status": decision.status,
                "rule_category": decision.food_category,
                "rule_reason": decision.reason,
                "rule_food_type": decision.food_key,
            })
            by_chain[CHAINS[dealer]] += 1
            by_status[decision.status] += 1
    # Do not produce an output silently missing offers from the validated inputs.
    if not records:
        raise ExportError("no source offer text in saved Tjek archives")
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        for row in records
    ).encode("utf-8")
    if len(payload) > MAX_EXPORT_BYTES:
        raise ExportError("minimal source text export exceeds bounded size")
    return payload, {
        "export_kind": "task041_personal_github_review_text_only",
        "source": "existing_saved_nine_chain_tjek_archive",
        "records": len(records),
        "chains": len(by_chain),
        "by_chain": dict(sorted(by_chain.items())),
        "by_rule_status": dict(sorted(by_status.items())),
        "source_declared_count": declared,
        "source_count_shortfall": declared - len(records),
        "output_bytes": len(payload),
        "note": (
            "Owner-permitted personal review only. Text descriptions can contain "
            "promotional pricing; no structured price, source identifiers, GTIN, "
            "original API payload, credentials or store coordinates exported. "
            "For owner-authorized review, transfer into this same private GitHub repository as an unreferenced blob, not a code PR."
        ),
        "http_requests": 0,
        "source_database_writes": 0,
    }


def write_private_export(target: Path, payload: bytes, *, private_root: Path) -> None:
    """Exclusive mode-0600 output, confined to ignored NutriValue data/private/."""
    try:
        root = private_root.resolve(strict=True)
        output = target.resolve(strict=False)
    except OSError as exc:
        raise ExportError("private export destination is unavailable") from exc
    if (not root.is_dir() or root not in output.parents
            or target.suffix != ".jsonl" or target.is_symlink()):
        raise ExportError("export destination must be .jsonl inside data/private/")
    if output.exists():
        raise ExportError("private export exists; refusing overwrite")
    if not output.parent.is_dir():
        raise ExportError("private export directory is unavailable")
    if len(payload) > MAX_EXPORT_BYTES or not payload:
        raise ExportError("invalid bounded export payload")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(output, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
    except OSError as exc:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise ExportError("could not write owner-private export") from exc
