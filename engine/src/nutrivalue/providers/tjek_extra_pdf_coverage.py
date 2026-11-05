"""TASK-032: zero-network Coop Øst PDF vs geo-selected Tjek offer coverage.

Provider's declared count (118) and accessible /v2/offers rows (103)
are DIFFERENT measurements. A PDF text heuristic cannot manufacture the
15 missing structured offers or prove count semantics; preserve explicit
unknown classification and private, review-only candidate evidence.
No OCR, HTTP, SQLite mutations, or sensitive Git output.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from nutrivalue.providers.tjek_extra_official import (
    MAX_PDF_BYTES, _hit, _norm, pdf_text_local, validate_official_text,
)
from nutrivalue.providers.tjek_weekly_collect import (
    CollectionError, _aware, _private_save, _safe_id, _week,
)
from nutrivalue.providers.tjek_offline import MAX_FILE_BYTES, TjekSnapshotError, parse_snapshot

REPORT_NAME = "extra-pdf-coverage-v1.json"
PRICE_RX = re.compile(
    r"(?:\b\d{1,4}[,.]\d{2}\b|\b\d{1,3}\s+\d{2}\b|\b\d{1,4}\s*,-)"
)
STOP_HEADINGS = {
    "coop", "extra", "tilbud", "medlemspris", "pr kg", "pr stk",
    "pr pk", "pr l", "førpris", "rabatt", "støtt rosa sløyfe",
    "sørg for", "gjelder", "produkt produkttekst", "produkt",
}


def _headings_from_pdf(text: str, known_headings: set[str]) -> list[dict]:
    """Bounded review-only text line candidates; NOT recovered provider offers.

    Uses the existing PDF text layer; no OCR. May include headings, groups,
    advertisements and normal copy. A printed heading is not a product row.
    """
    result: list[dict] = []
    seen: set[str] = set()
    for page_no, page in enumerate(text.split("\f"), 1):
        lines = page.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not 8 <= len(stripped) <= 110 or PRICE_RX.search(stripped):
                continue
            if not any(ch.isalpha() for ch in stripped):
                continue
            letters = [ch for ch in stripped if ch.isalpha()]
            if not letters or sum(ch.isupper() for ch in letters) / len(letters) < 0.78:
                continue
            candidate = _norm(stripped)
            if (len(candidate) < 8 or candidate in STOP_HEADINGS
                    or candidate in known_headings
                    or candidate.startswith(("pr ", "førpris", "uke ", "produkt "))
                    or candidate.startswith("tilbud")
                    or len(candidate.split()) < 2):
                continue
            # A nearby price is only a RELEVANCE HINT. It cannot prove the
            # printed price belongs to this title or is a Tjek offer.
            around = " ".join(lines[max(0, i-2):min(len(lines), i+5)])
            if not PRICE_RX.search(around):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            result.append({
                "page": page_no, "printed_heading_candidate": stripped[:110],
                "status": "PDF_TEXT_CANDIDATE_ONLY_UNVERIFIED",
            })
            if len(result) >= 120:
                return result
    return result


def pdf_coverage(*, pdf_text: str, as_of: str,
                 catalog: dict, offers: list[dict]) -> dict:
    """Compute accountable source coverage without equating 118 with PDF rows."""
    validate_official_text(pdf_text, as_of)
    if not isinstance(catalog, dict) or catalog.get("dealer_id") != "80742m":
        raise CollectionError("PDF audit requires Extra catalog")
    cid = _safe_id(catalog.get("id"))
    declared = catalog.get("offer_count")
    if type(declared) is not int or not 0 <= declared <= 5000:
        raise CollectionError("invalid declared Extra catalog count")
    if not isinstance(offers, list) or len(offers) > 5000:
        raise CollectionError("invalid saved Extra offers list")
    ids = set()
    matched = []
    unmatched = []
    pages = [_norm(page) for page in pdf_text.split("\f") if page.strip()]
    headings = set()
    for row in offers:
        if (not isinstance(row, dict) or row.get("catalog_id") != cid
                or row.get("dealer_id") != "80742m"):
            raise CollectionError("foreign or mismatched Extra offer in PDF audit")
        oid = _safe_id(row.get("id"))
        if oid in ids:
            raise CollectionError("duplicate Extra offer ID in private source snapshot")
        ids.add(oid)
        label = row.get("heading")
        if not isinstance(label, str) or not label.strip():
            raise CollectionError("missing saved Extra heading")
        headings.add(_norm(label))
        record = {
            "source_offer_id": oid,
            "source_heading": label,
            "source_advertised_price_nok": (row.get("pricing") or {}).get("price"),
            "status": "text_and_nearby_price_correlated" if _hit(row, pages)
                      else "not_correlated_by_text_heuristic",
        }
        (matched if record["status"] == "text_and_nearby_price_correlated"
         else unmatched).append(record)
    candidates = _headings_from_pdf(pdf_text, headings)
    return {
        "status": "source_only_pdf_coverage_review",
        "catalog_id": cid, "declared_catalog_offer_count": declared,
        "available_api_offer_rows": len(offers),
        "declared_minus_available": max(0, declared - len(offers)),
        "ordinary_pagination_exhausted": "owner_observed_offset_103_returned_empty",
        "official_pdf_region_week_validated": True,
        "pdf_pages_with_text": len(pages),
        "matched_tjek_rows_by_heuristic": len(matched),
        "unmatched_tjek_rows_by_heuristic": len(unmatched),
        "pdf_only_possible_heading_candidates": len(candidates),
        "tjek_source_offer_coverage": {
            "matched": matched, "unmatched": unmatched,
        },
        "pdf_heading_candidates_review_only": candidates,
        "unresolved_declared_shortfall": max(0, declared - len(offers)),
        "verified_new_missing_offers": 0,
        "pdf_brochure_items_not_equivalent_to_offer_count": True,
        "no_shelf_price_verification": True,
        "caveat": (
            "Text proximity may give false positives and false negatives. "
            "PDF headings might be group advertising or normal copy. "
            "No count of PDF candidate headings can establish which, if any, "
            "of the provider's declared but unexposed offers they represent."
        ),
    }


def audit_saved_extra_pdf(*, private_root: Path, compare_dir: Path,
                          geo_report_path: Path, as_of: str,
                          extract_text=pdf_text_local) -> dict:
    """Inspect archived six sources + official Øst PDF + *real* geo A/B proof.

    Zero HTTP, original files immutable; one new private report, never re-run
    over the same report. Prints no product names outside ignored folder.
    """
    at = _aware(as_of)
    root = Path(private_root).resolve(strict=True)

    def child(path: Path, parts: int) -> tuple[Path, tuple[str, ...]]:
        if Path(path).is_symlink():
            raise CollectionError("PDF audit archive paths cannot be symlinks")
        try:
            loc = Path(path).resolve(strict=True)
            rel = loc.relative_to(root).parts
        except (OSError, ValueError) as exc:
            raise CollectionError("PDF audit input outside private archive") from exc
        if len(rel) != parts or rel[0] != _week(at):
            raise CollectionError("PDF audit input wrong week/structure")
        return loc, rel

    folder, parts = child(compare_dir, 2)
    if not re.fullmatch(r"extra-compare-[A-Za-z0-9_-]{8,40}", parts[1]):
        raise CollectionError("expected private Extra comparison folder")
    geo, geo_parts = child(geo_report_path, 3)
    if (geo_parts[1] != "extra-api-investigation"
            or geo_parts[2] != "extra-geo-resolution-v1.json"):
        raise CollectionError("expected immutable Tjek Extra geo replay evidence")
    target = folder / REPORT_NAME
    if target.exists() or target.is_symlink():
        raise CollectionError("Extra PDF coverage audit already exists; preserve original")
    try:
        geo_evidence = json.loads(geo.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise CollectionError("invalid archived geo evidence") from exc
    if geo_evidence.get("status") != "geo_oslo_catalog_candidate" or geo_evidence.get("http_requests") != 0:
        raise CollectionError("geo evidence lacks successful owner-offline Oslo decision")
    cid = _safe_id(geo_evidence.get("oslo_candidate_catalog_id"))

    manifest_path = folder / "manifest.json"
    pdf_path = folder / "coop-ost.pdf"
    if manifest_path.is_symlink() or pdf_path.is_symlink():
        raise CollectionError("PDF audit raw sources cannot be symlinks")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise CollectionError("invalid original Extra archive manifest") from exc
    if manifest.get("week") != _week(at) or manifest.get("halted_reason"):
        raise CollectionError("archived Extra comparison incomplete/wrong week")
    entry = next(
        (row for row in manifest.get("attempts", [])
         if row.get("catalog_id") == cid and row.get("status") == "source_saved"),
        None,
    )
    if entry is None:
        raise CollectionError("geo selected catalog absent in original six-source archive")
    raw_path = folder / f"{cid}.json"
    if raw_path.is_symlink():
        raise CollectionError("provider source cannot be symlink")
    with raw_path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if not raw or len(raw) > MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        raise CollectionError("Extra saved JSON checksum or size mismatch")
    try:
        parsed = parse_snapshot(raw, f"{cid}.json")
        data = json.loads(raw)
    except (TjekSnapshotError, UnicodeError, ValueError) as exc:
        raise CollectionError("archived Extra source invalid") from exc
    if len(data.get("entries", [])) != 1 or len(parsed.offers) != entry.get("available_offers"):
        raise CollectionError("saved Extra provider row count inconsistent")
    item = data["entries"][0]
    with pdf_path.open("rb") as stream:
        pdf_bytes = stream.read(MAX_PDF_BYTES + 1)
    if not pdf_bytes.startswith(b"%PDF-") or len(pdf_bytes) > MAX_PDF_BYTES:
        raise CollectionError("missing or invalid original official Coop PDF")
    result = pdf_coverage(
        pdf_text=extract_text(pdf_path), as_of=as_of,
        catalog=item["catalog"], offers=item["offers"],
    )
    result.update({
        "schema_version": 1, "week": _week(at),
        "method": "archived_tjek_geo_selected_oslo_vs_original_official_coop_ost_pdf",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "geo_report_sha256": hashlib.sha256(geo.read_bytes()).hexdigest(),
        "http_requests": 0, "source_only": True,
        "original_files_unchanged": True,
    })
    _private_save(folder, REPORT_NAME, json.dumps(
        result, ensure_ascii=False, sort_keys=True, indent=2,
    ).encode("utf-8"))
    return result
