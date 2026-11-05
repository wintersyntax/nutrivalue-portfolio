#!/usr/bin/env python3
"""TASK-033: offline two-host schema comparison, one original Tjek catalog.

NO HTTP. One existing immutable private source snapshot is mandatory.
Optional second-host response must already be a privately saved JSON array
collected with provider-approved access. Never copies/prints raw offers.

Usage:
  ./scripts/ci-python scripts/compare_tjek_api_views.py \
    --original data/private/tjek/weekly/2026-W42/extra-compare-20261016T004643Z/W8l7eqAB.json \
    --catalog-id W8l7eqAB --dealer-id 80742m
  ./scripts/ci-python scripts/compare_tjek_api_views.py ... \
    --other data/private/tjek/api-view-compare/approved-etilbudsavis-response.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.tjek_api_schema_compare import (  # noqa: E402
    compare_api_views,
)
from nutrivalue.providers.tjek_offline import parse_snapshot  # noqa: E402

MAX_INPUT_BYTES = 12_000_000
MAX_OTHER_BYTES = 2_000_000


def _read_private(path: Path, *, root: Path, limit: int) -> tuple[bytes, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("private input is absent or symlink")
    selected = path.resolve(strict=True)
    selected.relative_to(root.resolve(strict=True))
    if selected.suffix.lower() != ".json":
        raise ValueError("private comparison input must be JSON")
    if selected.stat().st_size > limit:
        raise ValueError("oversized private comparison input")
    with selected.open("rb") as stream:
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("invalid input size")
    return data, json.loads(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--catalog-id", required=True)
    parser.add_argument("--dealer-id", required=True)
    parser.add_argument("--other", type=Path, default=None,
                        help="optional already-saved authorized API response JSON")
    args = parser.parse_args(argv)
    try:
        private_root = ROOT / "data/private/tjek"
        raw, original = _read_private(args.original, root=private_root,
                                     limit=MAX_INPUT_BYTES)
        validated = parse_snapshot(raw, args.original.name)
        if not any(catalog[1] == args.catalog_id for catalog in validated.catalogs):
            raise ValueError("selected original catalog missing")
        second = (
            _read_private(args.other, root=private_root,
                          limit=MAX_OTHER_BYTES)[1]
            if args.other is not None else None
        )
        report = compare_api_views(
            original, catalog_id=args.catalog_id,
            dealer_id=args.dealer_id, other_response=second)
    except (ValueError, OSError, UnicodeError, TypeError, KeyError) as exc:
        # Do not accidentally print input-derived product or pathname.
        print("TJEK HOST SHAPE: RED (" + type(exc).__name__ + ")",
              file=sys.stderr)
        return 1
    print("TJEK HOST SHAPE — OFFLINE, SOURCE_ONLY (0 HTTP, 0 writes)")
    for key, value in report.items():
        if key.endswith("_field_counts"):
            print(f"  {key}:")
            for field, count in value.items():
                print(f"    {field}={count}")
        else:
            print(f"  {key}: {value}")
    if second is None:
        print("NO OTHER HOST RESPONSE: original schema baseline only.")
        print("A same-catalog comparison remains PENDING authorized source capture.")
    else:
        print("Second response may be a first page/SAMPLE; unseen IDs are NOT "
              "proved missing. No price or variant automatically confirmed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
