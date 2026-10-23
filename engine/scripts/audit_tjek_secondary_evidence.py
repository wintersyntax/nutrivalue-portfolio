#!/usr/bin/env python3
"""TASK-033: OFFLINE, count-only source offer enrichment pilot.

Consumes exactly the existing nine-chain private archive. Optionally reads
manually saved, bounded eTilbudsavis structured offers from a PRIVATE file;
never makes requests to Tjek, eTilbudsavis, Kassalapp or any retailer.
No original names, IDs, prices, EANs or source JSON are printed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_offline import (  # noqa: E402
    CHAINS, MAX_FILE_BYTES, parse_snapshot,
)
from nutrivalue.providers.tjek_pdf_link import _normalized  # noqa: E402
from nutrivalue.providers.tjek_secondary_evidence import (  # noqa: E402
    _ore, diagnose_alternative_mass_basis, match_etilbudsavis_view,
    parse_carton_condition,
)
from nutrivalue.providers.tjek_unit_price import interpret_unit_price  # noqa: E402


def _private_file(path: Path, parent: Path, cap: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or symlinked private source")
    target = path.resolve(strict=True)
    target.relative_to(parent.resolve(strict=True))
    if target.stat().st_size > cap:
        raise ValueError("private source exceeds read budget")
    with target.open("rb") as fp:
        return fp.read(cap + 1)


def _candidate_key(x: dict, *, public: bool):
    if public:
        pricing = x.get("price")
        heading = x.get("name")
        dealer, catalog = x.get("businessPublicId"), x.get("publicationPublicId")
    else:
        pricing = (x.get("pricing") or {}).get("price")
        heading = x.get("heading")
        dealer, catalog = x.get("dealer_id"), x.get("catalog_id")
    if not all(isinstance(v, str) and v for v in (heading, dealer, catalog)):
        return None
    amount = _ore(pricing)
    if amount is None:
        return None
    return dealer, catalog, _normalized(heading), amount


def _optional_views(path: Path | None, private_root: Path) -> list[dict]:
    if path is None:
        return []
    raw = _private_file(path, private_root, 1_000_000)
    body = json.loads(raw)
    if (not isinstance(body, dict)
            or body.get("record_type") != "etilbudsavis-offers-v1"
            or not isinstance(body.get("offers"), list)
            or len(body["offers"]) > 500
            or any(not isinstance(x, dict) for x in body["offers"])):
        raise ValueError("invalid bounded private eTilbudsavis structured view")
    return body["offers"]


def summarize(private_root: Path, capture: Path,
              public_views: list[dict]) -> tuple[dict, dict]:
    root = private_root.resolve(strict=True)
    folder = capture.resolve(strict=True)
    pieces = folder.relative_to(root).parts
    if (capture.is_symlink() or len(pieces) != 2
            or not re.fullmatch(r"\d{4}-W\d{2}", pieces[0])
            or not re.fullmatch(r"run-[A-Za-z0-9_-]{8,40}", pieces[1])):
        raise ValueError("expected archived private YYYY-Www/run-* Tjek directory")
    rows: list[dict] = []
    counts = Counter()
    for dealer, chain in CHAINS.items():
        raw = _private_file(folder / f"{dealer}.json", root, MAX_FILE_BYTES)
        validated = parse_snapshot(raw, f"{dealer}.json")
        body = json.loads(raw)
        if {cat[2] for cat in validated.catalogs} != {dealer}:
            raise ValueError("source mixes retailer dealers")
        original = [offer for entry in body.get("entries", [])
                    for offer in entry["offers"]]
        if len(original) != len(validated.offers):
            raise ValueError("validated offer count differs from original")
        for offer in original:
            if offer.get("dealer_id") != dealer:
                raise ValueError("foreign retailer offer")
            rows.append(offer)
            counts[(chain, "offers")] += 1
            state = interpret_unit_price(offer)["state"]
            if state == "unexplained_unit_rate_mismatch":
                counts[(chain, "unexplained_unit_rate_mismatch")] += 1
            carton = parse_carton_condition(offer)
            if carton["state"] == "explicit_carton_basket_review_only":
                counts[(chain, "explicit_carton_text_review_only")] += 1
            elif carton["state"] == "ambiguous_carton_mentions":
                counts[(chain, "ambiguous_carton_mentions")] += 1
            mass = diagnose_alternative_mass_basis(offer)
            if mass["state"] == "arithmetic_smaller_mass_hypothesis_review_only":
                counts[(chain, "alternative_mass_arithmetic_only")] += 1

    original_by_key: dict[tuple, list[dict]] = defaultdict(list)
    for offer in rows:
        key = _candidate_key(offer, public=False)
        if key is not None:
            original_by_key[key].append(offer)
    public_by_key: dict[tuple, list[dict]] = defaultdict(list)
    for view in public_views:
        key = _candidate_key(view, public=True)
        if key is not None:
            public_by_key[key].append(view)
    by_chain = {dealer: chain for dealer, chain in CHAINS.items()}
    for key, views in public_by_key.items():
        dealer = key[0]
        if dealer not in by_chain:
            continue
        chain = by_chain[dealer]
        originals = original_by_key.get(key, [])
        if len(views) != 1 or len(originals) != 1:
            counts[(chain, "view_key_ambiguous_or_unmatched")] += len(views)
            continue
        result = match_etilbudsavis_view(originals[0], views[0])
        counts[(chain, "view_" + result["state"])] += 1
        if result["membership_price_review_only_ore"] is not None:
            counts[(chain, "view_member_candidate_review_only")] += 1
        if (result["buy_units_review_only"] is not None
                and result["pay_units_review_only"] is not None):
            counts[(chain, "view_buy_get_pay_count_review_only")] += 1
    rows_by_chain = {
        chain: {stat: count for (label, stat), count in counts.items()
                if label == chain}
        for chain in CHAINS.values()
    }
    totals = Counter()
    for data in rows_by_chain.values():
        totals.update(data)
    return rows_by_chain, dict(totals)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--etilbudsavis-json", type=Path,
                        help="optional already archived PRIVATE structured offers, never fetched")
    args = parser.parse_args(argv)
    root = ROOT / "data/private/tjek/weekly"
    try:
        views = _optional_views(args.etilbudsavis_json,
                                ROOT / "data/private/tjek")
        by_chain, totals = summarize(root, args.capture_dir, views)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Avoid printing exception messages derived from private input.
        print(f"SECONDARY EVIDENCE AUDIT RED: {type(exc).__name__}",
              file=sys.stderr)
        return 1
    for chain, values in by_chain.items():
        print(chain + ": " + ", ".join(
            f"{k}={n}" for k, n in sorted(values.items())))
    print("TOTAL: " + ", ".join(f"{k}={n}" for k, n in sorted(totals.items())))
    print("SOURCE_ONLY REVIEW_ONLY (no HTTP, files/DB writes, prices or EAN output).")
    print("eTilbudsavis fields, if supplied, share Tjek provenance; "
          "names without EAN are never matched to inventory SKUs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
