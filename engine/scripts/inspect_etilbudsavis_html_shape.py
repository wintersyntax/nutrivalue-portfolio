#!/usr/bin/env python3
"""TASK-033: inspect ONE already saved, private site HTML structure, 0 HTTP.

Print ONLY allowlisted aggregate counts. Do not upload, echo, open in
GitHub, or copy the source HTML into this repo's tracked files.
Example:
  ./scripts/ci-python scripts/inspect_etilbudsavis_html_shape.py \
    --html data/private/tjek/api-view-compare/etilbudsavis-husholdning-20261016T150436984303Z.html \
    --expect-sha256 1c2c9c9f53b32d67c74f7a117fe2bb63a58a540193dcc937ea3b957378f56d28
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_site_shape import diagnose_site_html_shape  # noqa: E402
from nutrivalue.providers.tjek_site_structured import (  # noqa: E402
    MAX_HTML_BYTES, SiteShapeError, extract_structured_offers,
)


def inspect_private_html(path: Path, *, expected_sha256: str,
                         private_root: Path, catalog_id: str,
                         parse_offers: bool = False) -> dict:
    """No symlinks, only the existing private site snapshot, never HTTP."""
    if (not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None):
        raise ValueError("expected 64-character lowercase SHA-256")
    if not path.is_file() or path.is_symlink():
        raise ValueError("private HTML must be a regular non-symlink file")
    root = private_root.resolve(strict=True)
    expected_dir = root / "api-view-compare"
    if expected_dir.is_symlink():
        raise ValueError("private directory may not be symlinked")
    resolved = path.resolve(strict=True)
    if resolved.parent != expected_dir.resolve(strict=True):
        raise ValueError("HTML must be directly inside private api-view-compare")
    if re.fullmatch(r"etilbudsavis-husholdning-\d{8}T\d{12}Z\.html",
                    resolved.name) is None:
        raise ValueError("not the expected one-page site source filename")
    if not 0 < resolved.stat().st_size <= MAX_HTML_BYTES:
        raise ValueError("HTML file size outside bounds")
    with resolved.open("rb") as handle:
        data = handle.read(MAX_HTML_BYTES + 1)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("archived HTML digest does not match")
    report = diagnose_site_html_shape(data, target_catalog=catalog_id)
    if parse_offers:
        try:
            rows = extract_structured_offers(data)
        except SiteShapeError:
            report.update(parser_status="saved_html_parser_shape_unrecognized",
                          recognized_offers=0)
        else:
            report.update(parser_status="site_offers_recognized",
                          recognized_offers=len(rows))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--expect-sha256", required=True)
    parser.add_argument("--catalog-id", default="W8l7eqAB")
    parser.add_argument("--parse-offers", action="store_true",
                        help="also count supported complete offers; no payload output")
    args = parser.parse_args(argv)
    try:
        shape = inspect_private_html(
            args.html, expected_sha256=args.expect_sha256,
            private_root=ROOT / "data/private/tjek",
            catalog_id=args.catalog_id, parse_offers=args.parse_offers)
    except (ValueError, OSError, TypeError, UnicodeError) as exc:
        print(f"ARCHIVED SITE SHAPE: RED ({type(exc).__name__})",
              file=sys.stderr)
        return 1
    print("ARCHIVED ETILBUDSAVIS HTML SHAPE — OFFLINE, COUNTS ONLY")
    print("  source_sha256_matched=True")
    for name, value in sorted(shape.items()):
        print(f"  {name}={value}")
    print("No HTTP, no source/DB writes, no product titles, no prices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
