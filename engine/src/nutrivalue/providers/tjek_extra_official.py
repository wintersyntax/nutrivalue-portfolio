"""TASK-032: official Coop Extra Øst PDF as an independent regional witness.

No undocumented Coop API is assumed. Compare only candidate-discriminating
facts from already PRIVATE Tjek JSON against the *text layer* of Coop's
official dated Øst PDF. This is evidence, not retail shelf-price validation.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from nutrivalue.providers.tjek_extra_region import DEALER, _fact
from nutrivalue.providers.tjek_weekly_collect import CollectionError, _aware, _private_save, _safe_id, _week

BASE = "https://kundeavis.coop.no/aviser/extra/ost"
MAX_PDF_BYTES = 15_000_000
MIN_UNIQUE_EVIDENCE = 3


def official_pdf_url(at: str) -> str:
    parsed = _aware(at)
    return f"{BASE}/ost-{parsed.isocalendar().week}.pdf"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    return " ".join(re.findall(r"[^\W_]+", s, re.UNICODE))


def _week_label(text: str, date: datetime) -> bool:
    week = date.isocalendar().week
    year = date.isocalendar().year
    return bool(re.search(
        rf"(?:uke\s*{week}\b.{{0,45}}\b{year}\b|"
        rf"\b{year}\b.{{0,45}}uke\s*{week}\b)",
        text, flags=re.IGNORECASE | re.DOTALL,
    ))


def validate_official_text(text: str, as_of: str) -> None:
    """A PDF without a visible ØST marker AND correct ISO week is not evidence."""
    date = _aware(as_of)
    if not isinstance(text, str) or not text or len(text) > 8_000_000:
        raise CollectionError("empty or excessive official Coop PDF text")
    if not re.search(r"(?<![^\W\d_])ØST(?![^\W\d_])", text, re.IGNORECASE):
        raise CollectionError("official PDF region ØST not identified")
    if not _week_label(text, date):
        raise CollectionError("official Coop PDF ISO week/year not identified")


def _price_forms(value) -> tuple[str, ...]:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0:
            return ()
        ore = amount * 100
        if ore != ore.to_integral_value():
            return ()
        whole, cents = divmod(int(ore), 100)
    except (InvalidOperation, TypeError, ValueError):
        return ()
    if cents:
        return (
            f"{whole}{cents:02d}", f"{whole} {cents:02d}",
        )
    return (f"{whole}", f"{whole}00", f"{whole} 00")


def _hit(offer: dict, pages: list[str]) -> bool:
    name = offer.get("heading")
    pricing = offer.get("pricing")
    if not isinstance(name, str) or not isinstance(pricing, dict):
        return False
    key = _norm(name)
    if len(key) < 7 or len(key.split()) < 2:
        return False
    price_forms = _price_forms(pricing.get("price"))
    if not price_forms:
        return False
    # Restrict to a single printed PDF page; avoid joining a name on one
    # page with a price on another. PDF text order may differ from layout,
    # so this is still supportive evidence rather than SKU-level proof.
    for page in pages:
        offset = 0
        while True:
            start = page.find(key, offset)
            if start < 0:
                break
            # Conservative local textual neighborhood: the title and numeric
            # price must appear close, not merely somewhere on the same page.
            nearby = page[max(0, start - 240):start + len(key) + 240]
            if any(re.search(rf"(?<!\d){re.escape(p)}(?!\d)", nearby)
                   for p in price_forms):
                return True
            offset = start + len(key)
    return False


def match_official_text(*, snapshots: dict[str, list[dict]],
                        official_text: str, as_of: str) -> dict:
    validate_official_text(official_text, as_of)
    if not snapshots or len(snapshots) > 9:
        raise CollectionError("expected bounded Extra candidate snapshots")
    pages = [_norm(p) for p in official_text.split("\f") if p.strip()]
    facts: dict[str, dict[str, dict]] = {}
    for cid, offers in snapshots.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,90}", cid):
            raise CollectionError("invalid candidate ID")
        if not isinstance(offers, list) or len(offers) > 5000:
            raise CollectionError("invalid candidate offers")
        entries = {}
        for o in offers:
            if not isinstance(o, dict) or o.get("dealer_id") != DEALER:
                raise CollectionError("foreign Extra offer")
            if o.get("catalog_id") not in (None, cid):
                raise CollectionError("mismatched Extra catalog ID")
            entries[_fact(o)] = o
        facts[cid] = entries
    all_sets = {cid: set(rows) for cid, rows in facts.items()}
    intersection = set.intersection(*all_sets.values())
    scores = []
    for cid in sorted(facts):
        distinctive = all_sets[cid] - intersection
        hits = sorted(k for k in distinctive if _hit(facts[cid][k], pages))
        scores.append({
            "catalog_id": cid,
            "unique_source_facts": len(distinctive),
            "official_pdf_matches": len(hits),
            # No raw product text or price exported to the report.
        })
    ordered = sorted(scores, key=lambda z: (-z["official_pdf_matches"], z["catalog_id"]))
    leader = ordered[0]
    runner_up = ordered[1]["official_pdf_matches"] if len(ordered) > 1 else 0
    unique_winner = (
        len(ordered) > 1
        and leader["official_pdf_matches"] >= MIN_UNIQUE_EVIDENCE
        and leader["official_pdf_matches"] - runner_up >= 2
    )
    return {
        "status": "ost_content_correlated" if unique_winner else "region_unresolved",
        "target": "Coop Øst",
        "pdf_week": _week(_aware(as_of)),
        "candidate_count": len(facts),
        "shared_source_facts": len(intersection),
        "scores": scores,
        "candidate_for_ost": leader["catalog_id"] if unique_winner else None,
        "verified_store_shelf_price": False,
        "official_text_region_and_week_verified": True,
        "method": "per-page product-heading and advertised-price matches on source-distinct offers",
        "caveat": (
            "An independently corroborated high-confidence regional candidate "
            "is not Tjek's authoritative region/store mapping. Missing PDF "
            "text or tied candidates remain unresolved."
        ),
    }


def fetch_official_pdf(url: str, *, opener=urllib.request.urlopen) -> bytes:
    url_parsed = urllib.parse.urlsplit(url)
    if (url_parsed.scheme != "https" or url_parsed.netloc != "kundeavis.coop.no"
            or not re.fullmatch(r"/aviser/extra/ost/ost-\d{1,2}\.pdf", url_parsed.path)
            or url_parsed.query or url_parsed.fragment):
        raise CollectionError("unapproved official PDF URL")
    req = urllib.request.Request(url, headers={
        "Accept": "application/pdf",
        "User-Agent": "NutriValue-private-region-check/0.1",
    }, method="GET")
    try:
        with opener(req, timeout=20) as response:
            target = urllib.parse.urlsplit(response.geturl())
            if target.scheme != "https" or target.netloc != "kundeavis.coop.no":
                raise CollectionError("official PDF redirected off allowed Coop host")
            if response.status != 200:
                raise CollectionError(f"Coop PDF HTTP {response.status}")
            data = response.read(MAX_PDF_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CollectionError(f"Coop PDF HTTP {exc.code}") from exc
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise CollectionError("Coop PDF download failed") from exc
    if not data.startswith(b"%PDF-") or len(data) > MAX_PDF_BYTES:
        raise CollectionError("invalid or oversized official Coop PDF")
    return data


def pdf_text_local(pdf_path: Path) -> str:
    """Use a real PDF text extractor, never OCR or regex the compressed bytes."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise CollectionError("pdftotext unavailable; install poppler or keep region unresolved")
    try:
        run = subprocess.run(
            [exe, "-layout", str(pdf_path), "-"],
            capture_output=True, timeout=30, check=True,
        )
        return run.stdout.decode("utf-8")
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError,
            UnicodeError) as exc:
        raise CollectionError("official PDF text extraction failed") from exc



def replay_saved_official(*, compare_dir: Path, private_root: Path, as_of: str,
                          extract_text=pdf_text_local) -> dict:
    """Offline-only immutable replay of existing Extra sources + Coop Øst PDF.

    This never invokes either Tjek or Coop HTTP; previously captured source
    facts and the original official-ost-match.json are not modified.
    """
    root = Path(private_root).resolve(strict=True)
    candidate = Path(compare_dir)
    if candidate.is_symlink():
        raise CollectionError("comparison directory cannot be symlink")
    try:
        folder = candidate.resolve(strict=True)
        parts = folder.relative_to(root).parts
    except (OSError, ValueError) as exc:
        raise CollectionError("comparison directory outside private Tjek archive") from exc
    if (len(parts) != 2 or parts[0] != _week(_aware(as_of))
            or not re.fullmatch(r"extra-compare-[A-Za-z0-9_-]{8,40}", parts[1])):
        raise CollectionError("expected private weekly Extra comparison archive")
    replay_path = folder / "official-ost-match-replay-v2.json"
    if replay_path.exists() or replay_path.is_symlink():
        raise CollectionError("offline replay evidence already exists; never overwrite")

    pdf_path = folder / "coop-ost.pdf"
    if pdf_path.is_symlink() or not pdf_path.is_file():
        raise CollectionError("missing or symlinked existing official Coop PDF")
    with pdf_path.open("rb") as stream:
        pdf_bytes = stream.read(MAX_PDF_BYTES + 1)
    if not pdf_bytes.startswith(b"%PDF-") or len(pdf_bytes) > MAX_PDF_BYTES:
        raise CollectionError("archived Coop PDF invalid or oversized")

    manifest_path = folder / "manifest.json"
    if manifest_path.is_symlink():
        raise CollectionError("Extra source manifest cannot be symlink")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise CollectionError("invalid saved Extra comparison manifest") from exc
    attempts = manifest.get("attempts")
    if manifest.get("halted_reason") or not isinstance(attempts, list) or not 1 <= len(attempts) <= 9:
        raise CollectionError("offline region replay requires completed Extra source capture")
    if manifest.get("week") != _week(_aware(as_of)) or manifest.get("run_directory") != folder.name:
        raise CollectionError("saved Extra source batch mismatches archive week/run")
    from nutrivalue.providers.tjek_offline import parse_snapshot
    rows: dict[str, list[dict]] = {}
    shortfalls = 0
    for entry in attempts:
        if not isinstance(entry, dict) or entry.get("status") != "source_saved":
            raise CollectionError("Extra source candidate was not fully saved")
        cid = _safe_id(entry.get("catalog_id"))
        if cid in rows:
            raise CollectionError("duplicate Extra catalog candidate")
        source = folder / f"{cid}.json"
        if source.is_symlink() or not source.is_file():
            raise CollectionError("Extra source file missing or is symlink")
        with source.open("rb") as stream:
            data = stream.read(5_000_001)
        if len(data) > 5_000_000 or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise CollectionError("saved Extra source digest or size mismatch")
        try:
            parsed = parse_snapshot(data, f"{cid}.json")
            envelope = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise CollectionError("invalid archived Extra source data") from exc
        if len(parsed.offers) != entry.get("available_offers"):
            raise CollectionError("saved Extra offer count changed")
        # TASK-023 parser checks identity, dealer, price and source conditions.
        rows[cid] = envelope["entries"][0]["offers"]
        shortfalls += max(0, entry.get("shortfall", 0))
    result: dict
    try:
        extracted = extract_text(pdf_path)
        result = match_official_text(
            snapshots=rows, official_text=extracted, as_of=as_of,
        )
        if result["status"] != "ost_content_correlated":
            result["unresolved_reason"] = "insufficient_or_tied_distinctive_offer_evidence"
    except CollectionError as exc:
        result = {
            "status": "region_unresolved",
            "candidate_for_ost": None,
            "unresolved_reason": f"official_pdf_validation_or_text_failure: {exc}",
            "official_text_region_and_week_verified": False,
        }
    result.update({
        "schema_version": 2,
        "method_run": "offline_replay_existing_coop_pdf",
        "tjek_http_requests": 0,
        "coop_http_requests": 0,
        "source_catalog_count": len(rows),
        "source_declared_offer_shortfall_sum": shortfalls,
        "official_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "original_report_preserved": True,
        "verified_store_shelf_price": False,
    })
    _private_save(
        folder, replay_path.name,
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )
    return result


def match_saved_private(*, compare_dir: Path, private_root: Path, as_of: str,
                        fetch_pdf=fetch_official_pdf,
                        extract_text=pdf_text_local) -> dict:
    """One official PDF read, zero Tjek requests, immutable private match.

    Paths must refer to the original bounded owner-private comparison archive.
    """
    root = Path(private_root).resolve(strict=True)
    if Path(compare_dir).is_symlink():
        raise CollectionError("comparison directory must not be symlink")
    try:
        folder = Path(compare_dir).resolve(strict=True)
        pieces = folder.relative_to(root).parts
    except (OSError, ValueError) as exc:
        raise CollectionError("comparison directory outside private Tjek archive") from exc
    if (len(pieces) != 2 or pieces[0] != _week(_aware(as_of))
            or not re.fullmatch(r"extra-compare-[A-Za-z0-9_-]{8,40}", pieces[1])):
        raise CollectionError("invalid Extra comparison archive structure")
    destination = folder / "official-ost-match.json"
    official_pdf = folder / "coop-ost.pdf"
    if destination.exists() or official_pdf.exists():
        raise CollectionError("official match was already attempted; never overwrite")
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("halted_reason") or len(manifest.get("attempts", [])) == 0:
        raise CollectionError("cannot region-match incomplete Extra source capture")
    rows: dict[str, list[dict]] = {}
    seen = set()
    from nutrivalue.providers.tjek_offline import parse_snapshot
    for entry in manifest["attempts"]:
        if entry.get("status") != "source_saved":
            raise CollectionError("Extra source candidate not saved")
        cid = _safe_id(entry.get("catalog_id"))
        if cid in seen:
            raise CollectionError("duplicate Extra candidate in saved manifest")
        seen.add(cid)
        raw_path = folder / f"{cid}.json"
        if raw_path.is_symlink():
            raise CollectionError("source offer snapshot symlink")
        snap_raw = raw_path.read_bytes()
        if hashlib.sha256(snap_raw).hexdigest() != entry["sha256"]:
            raise CollectionError("source snapshot checksum changed")
        parse_snapshot(snap_raw, f"{cid}.json")
        parsed = json.loads(snap_raw)
        rows[cid] = parsed["entries"][0]["offers"]
    url = official_pdf_url(as_of)
    official = fetch_pdf(url)
    _private_save(folder, "coop-ost.pdf", official)
    try:
        text = extract_text(official_pdf)
        result = match_official_text(snapshots=rows, official_text=text, as_of=as_of)
    except CollectionError as exc:
        # PDF and old source remain archived. Separate source-independent
        # status without printing raw data; failure is not false validation.
        result = {
            "status": "region_unresolved",
            "reason": str(exc),
            "candidate_for_ost": None,
        }
    result.update({
        "official_url": url,
        "official_pdf_sha256": hashlib.sha256(official).hexdigest(),
        "source_extra_comparison": folder.name,
        "tjek_http_requests": 0, "coop_http_requests": 1,
    })
    _private_save(folder, "official-ost-match.json", json.dumps(
        result, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
    return result
