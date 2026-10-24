#!/usr/bin/env python3
"""TASK-033: audit original PRIVATE Tjek offers for conditional prices.

Offline, read-only, no SQLite writes. Never prints source offer headings,
descriptions, member price amounts or private offer IDs. The original snapshots
remain untouched. Synthetic tests do not need personal Tjek data.

Example:
  ./scripts/ci-python scripts/audit_tjek_offer_conditions.py \
    --capture-dir data/private/tjek/weekly/2026-W42/run-20261016T031849Z
"""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from collections import Counter
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_offline import (  # noqa: E402
    CHAINS, MAX_FILE_BYTES, TjekSnapshotError, parse_snapshot,
)
from nutrivalue.providers.tjek_offer_conditions import (  # noqa: E402
    PERCENT, interpret_offer,
)
from nutrivalue.providers.tjek_unit_price import (  # noqa: E402
    interpret_unit_price, mismatch_clue_bucket,
    MIN_PACK_IN_TEXT, UNITS, _number,
)


def profile_price_fields(offer_objects: list[dict], interpretations: list[dict]) -> dict:
    """Aggregate only. No raw offer text, IDs, money amounts, or hashes printed.

    A price beside a percentage is NOT proof that percentage is additional;
    it may already be the discount-adjusted source price.
    """
    if len(offer_objects) != len(interpretations):
        raise ValueError("original/interpretation counts differ")
    totals = Counter()
    repeated_percentage_descriptions = Counter()
    for offer, item in zip(offer_objects, interpretations):
        source, old = item["source_price_ore"], item["source_pre_price_ore"]
        if source is not None:
            totals["price_present"] += 1
        if old is not None:
            totals["pre_price_present"] += 1
            if source is not None:
                totals["both_price_fields"] += 1
                if old > source:
                    totals["pre_price_above_source"] += 1
                elif old == source:
                    totals["pre_price_equal_source"] += 1
                else:
                    totals["pre_price_below_source"] += 1
        if item["percent_condition_count"]:
            totals["percentage_matches_unverified"] += 1
            if source is None:
                totals["percent_without_source_price"] += 1
            else:
                totals["percent_with_source_price"] += 1
            if old is not None:
                totals["percent_with_pre_price"] += 1
            if source is None and old is None:
                totals["percent_without_both_prices"] += 1
            heading = offer.get("heading", "")
            description = offer.get("description") or ""
            if PERCENT.search(heading):
                totals["percent_literal_in_heading"] += 1
            if PERCENT.search(description):
                totals["percent_literal_in_description"] += 1
            # Counts shared source wording without revealing or uploading text.
            normalized = re.sub(r"\s+", " ", description.strip().casefold())
            if normalized:
                digest = hashlib.sha256(normalized.encode("utf-8")).digest()
                repeated_percentage_descriptions[digest] += 1
    totals["distinct_percent_descriptions"] = len(repeated_percentage_descriptions)
    totals["max_same_percent_description"] = max(
        repeated_percentage_descriptions.values(), default=0)
    return dict(totals)


def schema_field_counts(offer_objects: list[dict]) -> dict[str, dict[str, int]]:
    """Count harmless provider field *names*, never their private values."""
    offer_keys, pricing_keys = Counter(), Counter()
    safe_key = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
    for offer in offer_objects:
        for key in offer:
            if isinstance(key, str) and safe_key.fullmatch(key):
                offer_keys[key] += 1
        pricing = offer.get("pricing")
        if isinstance(pricing, dict):
            for key in pricing:
                if isinstance(key, str) and safe_key.fullmatch(key):
                    pricing_keys[key] += 1
    return {"offer_keys": dict(offer_keys),
            "pricing_keys": dict(pricing_keys)}




def profile_minimum_pack_evidence(offer_objects: list[dict]) -> dict[str, int]:
    """Count source-only Fra pack cues; no product/title/price output.

    Does not alter the underlying interpretation. An API '2 for X'
    coexisting with 'Fra 80 g' is review-only, not an eligible kg rate.
    """
    out = Counter()
    for offer in offer_objects:
        assessment = interpret_unit_price(offer)
        if assessment["state"] != "pack_size_minimum_in_description":
            continue
        out["guarded_offers"] += 1
        description = offer.get("description") or ""
        basis = assessment["basis"]
        matches = [
            hit for hit in MIN_PACK_IN_TEXT.finditer(description)
            if UNITS[hit.group("unit").casefold()][0] == basis
        ]
        if not matches:
            raise ValueError("minimum guard reported without matching Fra evidence")
        out["single_floor_cue" if len(matches) == 1 else "multiple_floor_cues"] += 1
        bundle = interpret_offer(offer)
        if bundle["bundle_units"] is not None:
            out["api_explicit_multibuy_and_floor"] += 1
        if bundle["percent_condition_count"]:
            out["percent_text_and_floor"] += 1
        if bundle["review_only_possible_member_ore"] is not None:
            out["member_candidate_and_floor"] += 1
        if any(re.search(r"\b(?:pr\.?\s*(?:pk|stk)|pakning)\b", description[
                hit.end():hit.end() + 35], re.I) for hit in matches):
            out["floor_next_to_pack_label"] += 1
        q = offer.get("quantity")
        unit = q.get("unit") if isinstance(q, dict) else None
        size = q.get("size") if isinstance(q, dict) else None
        if not isinstance(unit, dict) or not isinstance(size, dict):
            out["floor_vs_source_size_unavailable"] += 1
            continue
        symbol = unit.get("symbol")
        if not isinstance(symbol, str) or symbol.casefold() not in UNITS:
            out["floor_vs_source_size_unavailable"] += 1
            continue
        source_basis, source_factor = UNITS[symbol.casefold()]
        if source_basis != basis:
            out["floor_vs_source_size_basis_mismatch"] += 1
            continue
        source_size = _number(size.get("from"))
        if source_size is None:
            out["floor_vs_source_size_unavailable"] += 1
            continue
        floors = [
            _number(hit.group("amount")) * UNITS[hit.group("unit").casefold()][1]
            for hit in matches
        ]
        if any(floor is None for floor in floors):
            out["floor_vs_source_size_unavailable"] += 1
            continue
        source_si = source_size * source_factor
        if len(floors) != 1:
            out["floor_vs_source_size_multiple_floors"] += 1
        elif floors[0] == source_si:
            out["floor_equals_source_size"] += 1
        elif floors[0] < source_si:
            out["floor_less_than_source_size"] += 1
        else:
            out["floor_greater_than_source_size"] += 1
    return dict(out)

def summarize(capture_dir: Path, private_root: Path, *, unit_prices: bool = False,
              mismatch_clues: bool = False, minimum_pack_diagnose: bool = False) -> list[dict]:
    """Validate exactly nine immutable original snapshots, return counts only."""
    if capture_dir.is_symlink():
        raise ValueError("capture directory must not be a symlink")
    root = private_root.resolve(strict=True)
    folder = capture_dir.resolve(strict=True)
    parts = folder.relative_to(root).parts
    if (len(parts) != 2 or not re.fullmatch(r"\d{4}-W\d{2}", parts[0])
            or not re.fullmatch(r"run-[A-Za-z0-9_-]{8,40}", parts[1]) or not folder.is_dir()):
        raise ValueError("expected one archived private YYYY-Www/run-* directory")
    results = []
    for dealer, name in CHAINS.items():
        path = folder / f"{dealer}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing or symlinked source file for {name}")
        with path.open("rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        snapshot = parse_snapshot(raw, path.name)
        if {row[2] for row in snapshot.catalogs} != {dealer}:
            raise ValueError(f"foreign source dealer for {name}")
        count = Counter({"offers": len(snapshot.offers)})
        # The staged tuple stores selected fields, not the entire provider
        # description/heading/pricing object. Interpret ORIGINAL JSON below.
        body = json.loads(raw)
        records = body.get("entries", [])
        offer_objects = [offer for group in records for offer in group["offers"]]
        if len(offer_objects) != len(snapshot.offers):
            raise ValueError(f"validated/raw offer count mismatch for {name}")
        interpretations = [interpret_offer(offer) for offer in offer_objects]
        for item in interpretations:
            if item["explicit_nonmember_ore"] is not None:
                count["explicit_nonmember"] += 1
            if item["conditional_member_ore"] is not None:
                count["member_price_with_text_evidence"] += 1
            if item["review_only_possible_member_ore"] is not None:
                count["member_candidate_review_only"] += 1
            if item["bundle_units"] is not None:
                count["multibuy_with_total"] += 1
            if item["percent_condition_count"]:
                count["percentage_condition"] += 1
            if item["explicit_previous_ore"] is not None:
                count["printed_previous_price"] += 1
            if item["pant_mentioned"]:
                count["pant"] += 1
            if item["review_reasons"]:
                count["needs_review"] += 1
        unit_states = Counter()
        unit_bases = Counter()
        mismatch_hints = Counter()
        minimum_profile = Counter()
        if unit_prices:
            for offer in offer_objects:
                assessment = interpret_unit_price(offer)
                unit_states[assessment["state"]] += 1
                unit_bases[assessment["basis"] or "unknown"] += 1
                if (mismatch_clues and
                        assessment["state"] == "unexplained_unit_rate_mismatch"):
                    mismatch_hints[mismatch_clue_bucket(offer, assessment)] += 1
        if minimum_pack_diagnose:
            minimum_profile = profile_minimum_pack_evidence(offer_objects)
        results.append({"chain": name, **count,
                        "_unit_price_states": dict(unit_states),
                        "_unit_price_bases": dict(unit_bases),
                        "_unit_mismatch_clues": dict(mismatch_hints),
                        "_minimum_pack_profile": dict(minimum_profile),
                        "_price_field_profile":
                            profile_price_fields(offer_objects, interpretations),
                        "_schema_field_counts": schema_field_counts(offer_objects)})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", required=True, type=Path)
    parser.add_argument("--diagnose", action="store_true",
                        help="also show aggregate price-field and percent-text shape counts")
    parser.add_argument("--unit-prices", action="store_true",
                        help="offline source-only per-kg/l evidence diagnostics, counts only")
    parser.add_argument("--unit-mismatch-clues", action="store_true",
                        help="only with --unit-prices: private mismatch clues (count buckets only)")
    parser.add_argument("--minimum-pack-diagnose", action="store_true",
                        help="only with --unit-prices: grouped Fra-size/bundle evidence counts")
    args = parser.parse_args(argv)
    if args.unit_mismatch_clues and not args.unit_prices:
        parser.error("--unit-mismatch-clues requires --unit-prices")
    if args.minimum_pack_diagnose and not args.unit_prices:
        parser.error("--minimum-pack-diagnose requires --unit-prices")
    try:
        rows = summarize(
            args.capture_dir,
            ROOT / "data/private/tjek/weekly", unit_prices=args.unit_prices,
            mismatch_clues=args.unit_mismatch_clues,
            minimum_pack_diagnose=args.minimum_pack_diagnose,
        )
    except (OSError, ValueError, KeyError, TypeError, TjekSnapshotError) as exc:
        print(f"TJEK CONDITION AUDIT: RED — {exc}", file=sys.stderr)
        return 1
    keys = (
        "offers", "explicit_nonmember", "member_price_with_text_evidence",
        "member_candidate_review_only", "multibuy_with_total",
        "percentage_condition", "printed_previous_price", "pant", "needs_review",
    )
    for row in rows:
        print(row["chain"] + ": " +
              ", ".join(f"{key}={row.get(key, 0)}" for key in keys))
        if args.unit_prices:
            unit_counts = row["_unit_price_states"]
            print("  unit_price_states: " + ", ".join(
                f"{name}={value}" for name, value in sorted(unit_counts.items())))
            unit_bases = row["_unit_price_bases"]
            print("  unit_price_bases: " + ", ".join(
                f"{name}={value}" for name, value in sorted(unit_bases.items())))
            if args.unit_mismatch_clues:
                clues = row["_unit_mismatch_clues"]
                print("  unit_mismatch_clues: " + ", ".join(
                    f"{key}={value}" for key, value in sorted(clues.items())))
        if args.minimum_pack_diagnose:
            pmin = row["_minimum_pack_profile"]
            print("  minimum_pack_evidence: " + ", ".join(
                f"{key}={value}" for key, value in sorted(pmin.items())))
        if args.diagnose:
            p = row["_price_field_profile"]
            diagnostic_keys = (
                "price_present", "pre_price_present", "both_price_fields",
                "pre_price_above_source", "pre_price_equal_source",
                "pre_price_below_source", "percentage_matches_unverified",
                "percent_with_source_price", "percent_without_source_price",
                "percent_with_pre_price", "percent_without_both_prices",
                "percent_literal_in_heading", "percent_literal_in_description",
                "distinct_percent_descriptions", "max_same_percent_description",
            )
            print("  field_profile: " +
                  ", ".join(f"{key}={p.get(key, 0)}" for key in diagnostic_keys))
            schemas = row["_schema_field_counts"]
            for category in ("offer_keys", "pricing_keys"):
                items = sorted(schemas[category].items())[:30]
                print("  " + category + ": " +
                      ", ".join(f"{key}={count}" for key, count in items))
    print("TOTAL: " +
          ", ".join(f"{key}={sum(row.get(key, 0) for row in rows)}" for key in keys))
    if args.unit_prices:
        aggregate = Counter()
        for row in rows:
            aggregate.update(row["_unit_price_states"])
        print("UNIT PRICE TOTAL: " + ", ".join(
            f"{k}={v}" for k, v in sorted(aggregate.items())))
        print("UNIT PRICE PROVENANCE: original Tjek offer text, NOT independent PDF.")
        if args.unit_mismatch_clues:
            clue_totals = Counter()
            for row in rows:
                clue_totals.update(row["_unit_mismatch_clues"])
            print("MISMATCH CLUES TOTAL: " + ", ".join(
                f"{key}={value}" for key, value in sorted(clue_totals.items())))
            print("Clues are NOT proven causes; check official source before trusting any rate.")
        if args.minimum_pack_diagnose:
            min_totals = Counter()
            for row in rows:
                min_totals.update(row["_minimum_pack_profile"])
            print("MINIMUM PACK PROFILE TOTAL: " + ", ".join(
                f"{key}={value}" for key, value in sorted(min_totals.items())))
            print("Fra is a lower-bound evidence cue, not proof of a fixed package size.")
        print("Undeclared 2-for/3-for PDF conditions may still exist: all unit rates REVIEW ONLY.")
    print("Offline source_only description audit; no files/DB/network writes.")
    print("Member candidates and unknown price kinds are NOT checkout/rank eligible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
