#!/usr/bin/env python3
"""TASK-033 Extra publisher PDF/API join — OFFLINE, read-only and REVIEW-ONLY.

Usage:
  ./scripts/ci-python scripts/link_tjek_extra_pdf.py \
    --snapshot data/private/tjek/weekly/2026-W42/extra-compare-20261016T004643Z/W8l7eqAB.json \
    --pdf data/private/tjek/weekly/2026-W42/extra-compare-20261016T004643Z/coop-ost.pdf \
    --as-of 2026-10-16T05:20:00+02:00
Prints aggregate counts only. Does NOT create/modify any files, compute new
member/bundle prices, or access network. No inferred shelf price or SKU.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_extra_official import (  # noqa: E402
    MAX_PDF_BYTES, pdf_text_local, validate_official_text,
)
from nutrivalue.providers.tjek_offline import (  # noqa: E402
    MAX_FILE_BYTES, parse_snapshot, TjekSnapshotError,
)
from nutrivalue.providers.tjek_pdf_link import (  # noqa: E402
    match_page_titles, count_states, physical_text_pages,
)
from nutrivalue.providers.tjek_pdf_geometry import (  # noqa: E402
    MAX_BBOX_BYTES, parse_bbox_pages, match_multibuy_badges,
    summarize_badge_candidates,
)
from nutrivalue.providers.tjek_pdf_member import (  # noqa: E402
    correlate_member_badges, member_badge_summary,
)
from nutrivalue.providers.tjek_unit_price import (  # noqa: E402
    condition_unit_prices_from_pdf_bundles, pdf_bundle_unit_summary,
)
from nutrivalue.providers.tjek_weekly_collect import CollectionError  # noqa: E402


def _private_source(path: Path, cap: int, *, pdf: bool) -> bytes:
    root = (ROOT / "data/private").resolve(strict=True)
    if path.is_symlink():
        raise ValueError("symlinked inputs not permitted")
    actual = path.resolve(strict=True)
    actual.relative_to(root)
    if not actual.is_file():
        raise ValueError("not a source file")
    with actual.open("rb") as file:
        data = file.read(cap + 1)
    if not data or len(data) > cap or (pdf and not data.startswith(b"%PDF-")):
        raise ValueError("source file invalid or exceeds size budget")
    return data


def _publisher_bbox(pdf_path: Path):
    """One local Poppler call; no generated files, no OCR or HTTP."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise ValueError("pdftotext unavailable for geometry")
    try:
        result = subprocess.run(
            [exe, "-bbox-layout", str(pdf_path), "-"],
            capture_output=True, timeout=40, check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("PDF bbox text extraction failed") from exc
    if len(result.stdout) > MAX_BBOX_BYTES:
        raise ValueError("PDF geometry exceeds bounded size")
    return parse_bbox_pages(result.stdout)


def audit(snapshot_path: Path, pdf_path: Path, as_of: str,
          *, geometry: bool = False, member: bool = False,
          unit_prices: bool = False) -> dict:
    raw = _private_source(snapshot_path, MAX_FILE_BYTES, pdf=False)
    _private_source(pdf_path, MAX_PDF_BYTES, pdf=True)
    validated = parse_snapshot(raw, snapshot_path.name)
    record = json.loads(raw)
    entries = record.get("entries", [])
    if len(entries) != 1:
        raise ValueError("requires one archived Extra catalog")
    entry = entries[0]
    catalog = entry["catalog"]
    if catalog["dealer_id"] != "80742m" or catalog["id"] != snapshot_path.stem:
        raise ValueError("not matching original Extra source")
    if len(entry["offers"]) != len(validated.offers):
        raise ValueError("source offer count changed since validation")
    text = pdf_text_local(pdf_path)
    validate_official_text(text, as_of)
    pages = physical_text_pages(text)
    if len(pages) < catalog.get("page_count", 0):
        raise ValueError("official PDF has too few text pages")
    for offer in entry["offers"]:
        if offer["catalog_id"] != catalog["id"]:
            raise ValueError("offer from foreign catalog")
    joined = match_page_titles(entry["offers"], pages)
    geometry_counts = {}
    if geometry or member or unit_prices:
        bbox_pages = _publisher_bbox(pdf_path)
        if len(bbox_pages) != len(pages):
            raise ValueError("PDF bbox/text physical page counts disagree")
        if geometry or unit_prices:
            badge_rows = match_multibuy_badges(entry["offers"], bbox_pages)
            if geometry:
                geometry_counts.update(summarize_badge_candidates(badge_rows))
            if unit_prices:
                geometry_counts.update(pdf_bundle_unit_summary(
                    condition_unit_prices_from_pdf_bundles(
                        entry["offers"], badge_rows)))
        if member:
            geometry_counts.update(member_badge_summary(
                correlate_member_badges(entry["offers"], bbox_pages)))
    return {
        **count_states(joined),
        **geometry_counts,
        "pdf_pages": len(pages),
        "pdf_pages_with_text": sum(bool(page.strip()) for page in pages),
        "pdf_pages_without_text": sum(not page.strip() for page in pages),
        "tjek_catalog_pages": catalog["page_count"],
        "declared_catalog_offers": catalog["offer_count"],
        "source_only": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--geometry", action="store_true",
                        help="review-only geometry corroboration of 2-for/3-for bundle totals")
    parser.add_argument("--member", action="store_true",
                        help="review-only PDF corroboration of API nonmember-minus-saving candidate")
    parser.add_argument("--unit-prices", action="store_true",
                        help="count conservative PDF-bundle-conditioned kg/l rates (review-only)")
    args = parser.parse_args(argv)
    try:
        counts = audit(args.snapshot, args.pdf, args.as_of,
                       geometry=args.geometry, member=args.member,
                       unit_prices=args.unit_prices)
    except (OSError, ValueError, KeyError, TypeError, TjekSnapshotError,
            CollectionError) as exc:
        print(f"TJEK PDF TITLE JOIN: RED — {exc}", file=sys.stderr)
        return 1
    print("TJEK PDF TITLE JOIN — SOURCE_ONLY (0 HTTP, 0 files written)")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print("Title+page and optional geometry totals are SOURCE_ONLY correlations.")
    print("PDF bundle/member/unit-rate correlations are REVIEW_ONLY: no member"
          " entitlement, variant/SKU, local shelf eligibility, group percentage,"
          " added discount arithmetic, or rank verification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
