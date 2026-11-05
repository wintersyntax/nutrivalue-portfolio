"""Offline, evidence-gated SKU/nutrition review. Never verifies a retail price.

Manufacturer evidence is manually transcribed into an ignored PRIVATE JSON file.
A provider nutrition value has no established per-100g basis; the manufacturer's
explicit as-sold, per-100g declaration is the only approved nutrition basis.
This module never inserts curated products, nutrition_profiles or prices.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from nutrivalue.gtin import canonical_gtin14

STATUSES = ("verified", "verified_with_warning", "needs_review")
SOURCE_ONLY = "source_only"
MAX_KCAL_WARNING = Decimal("20")
MAX_CARBS_WARNING_G = Decimal("1.5")


def _positive(value, maximum=None):
    if value is None or isinstance(value, bool):
        raise ValueError("Missing or invalid numeric manufacturer evidence")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid numeric manufacturer evidence") from exc
    if not result.is_finite() or result <= 0 or (maximum is not None and result > maximum):
        raise ValueError("Out-of-range manufacturer evidence")
    return result


def _nonnegative(value, maximum=None):
    if value is None or isinstance(value, bool):
        raise ValueError("Missing or invalid numeric manufacturer evidence")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid numeric manufacturer evidence") from exc
    if not result.is_finite() or result < 0 or (maximum is not None and result > maximum):
        raise ValueError("Out-of-range manufacturer evidence")
    return result


def _gtin13(ean):
    """Preserve the existing strict 13-digit manufacturer evidence contract."""
    return (type(ean) is str and len(ean) == 13
            and canonical_gtin14(ean) is not None)


def _normal(text):
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class ManufacturerEvidence:
    ean: str
    brand: str
    name: str
    provider_names: tuple[str, ...]
    net_weight_g: Decimal
    protein_per_100g: Decimal
    kcal_per_100g: Decimal
    carbs_per_100g: Decimal
    source_url: str
    checked_on: str
    reviewer_confirmed: bool
    reviewer_note: str
    evidence_sha256: str
    raw_evidence_json: str


def parse_evidence(raw: dict) -> ManufacturerEvidence:
    if not isinstance(raw, dict):
        raise ValueError("Manufacturer evidence must be a JSON object")
    required = {
        "ean", "brand", "name", "provider_names", "net_weight_g",
        "protein_per_100g", "kcal_per_100g", "carbs_per_100g",
        "nutrition_basis", "source_url", "checked_on",
        "reviewer_confirmed", "reviewer_note",
    }
    if set(raw) != required:
        raise ValueError(f"Manufacturer evidence field mismatch: {sorted(required ^ set(raw))}")
    ean = raw["ean"]
    if not _gtin13(ean):
        raise ValueError("Manufacturer evidence needs valid 13-digit GTIN checksum")
    for key in ("brand", "name", "reviewer_note"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ValueError(f"Manufacturer evidence needs {key}")
    names = raw["provider_names"]
    if (not isinstance(names, list) or not names
            or any(not isinstance(x, str) or not x.strip() for x in names)
            or len({_normal(x) for x in names}) != len(names)):
        raise ValueError("provider_names must be distinct, exact, nonempty names")
    if raw["nutrition_basis"] != "per_100g_as_sold":
        raise ValueError("Manufacturer must explicitly attest per_100g_as_sold basis")
    if type(raw["reviewer_confirmed"]) is not bool:
        raise ValueError("reviewer_confirmed must be a boolean")
    url = raw["source_url"]
    if not isinstance(url, str):
        raise ValueError("Manufacturer source URL required")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Manufacturer source must use a non-credentialed HTTPS URL")
    checked = raw["checked_on"]
    if not isinstance(checked, str):
        raise ValueError("checked_on must be an ISO date")
    try:
        if date.fromisoformat(checked).isoformat() != checked:
            raise ValueError("Noncanonical checked_on date")
    except ValueError as exc:
        raise ValueError("Invalid checked_on date") from exc
    weight = _positive(raw["net_weight_g"])
    protein = _positive(raw["protein_per_100g"], Decimal("100"))
    kcal = _nonnegative(raw["kcal_per_100g"])
    carbs = _nonnegative(raw["carbs_per_100g"], Decimal("100"))
    if protein + carbs > Decimal("100"):
        raise ValueError("Impossible macronutrient totals")
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return ManufacturerEvidence(
        ean=ean, brand=raw["brand"].strip(), name=raw["name"].strip(),
        provider_names=tuple(names), net_weight_g=weight,
        protein_per_100g=protein, kcal_per_100g=kcal,
        carbs_per_100g=carbs, source_url=url, checked_on=checked,
        reviewer_confirmed=raw["reviewer_confirmed"],
        reviewer_note=raw["reviewer_note"].strip(),
        evidence_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        raw_evidence_json=canonical,
    )


@dataclass(frozen=True)
class Review:
    evidence: ManufacturerEvidence
    identity_status: str
    nutrition_status: str
    overall_status: str
    price_status: str
    reasons: tuple[str, ...]
    candidate_count: int
    review_sha256: str
    staging_ids_json: str


def _reported_mass(record):
    try:
        n = _positive(record.get("weight"))
    except ValueError:
        return None
    unit = record.get("weight_unit")
    if unit == "g":
        return n
    if unit == "kg":
        return n * 1000
    return None


def _provider_nutrient(record, code, unit):
    nutrients = record.get("nutrition")
    if not isinstance(nutrients, list):
        return None, False
    matches = [x for x in nutrients if isinstance(x, dict) and x.get("code") == code]
    if not matches:
        return None, False
    if len(matches) != 1 or matches[0].get("unit") != unit:
        return None, True
    try:
        return _nonnegative(matches[0].get("amount")), False
    except ValueError:
        return None, True


def review(evidence: ManufacturerEvidence, staging_rows: list[sqlite3.Row]) -> Review:
    reasons = set()
    confirmed = evidence.reviewer_confirmed
    if not confirmed:
        reasons.add("manual_manufacturer_review_pending")
    if not staging_rows:
        reasons.add("no_matching_staging_product")
    accepted = {_normal(n) for n in evidence.provider_names}
    for row in staging_rows:
        if row["ean"] != evidence.ean:
            reasons.add("ean_conflict")
        if _normal(row["product_name"]) not in accepted:
            reasons.add("variant_name_conflict")
        if not row["brand"] or _normal(row["brand"]) != _normal(evidence.brand):
            reasons.add("brand_conflict")
        try:
            source = json.loads(row["raw_payload_json"])
        except (TypeError, json.JSONDecodeError):
            reasons.add("invalid_provider_json")
            continue
        if not isinstance(source, dict) or str(source.get("ean")) != evidence.ean:
            reasons.add("provider_ean_conflict")
            continue
        if not isinstance(source.get("name"), str) or _normal(source["name"]) not in accepted:
            reasons.add("provider_variant_name_conflict")
        if not isinstance(source.get("brand"), str) or _normal(source["brand"]) != _normal(evidence.brand):
            reasons.add("provider_brand_conflict")
        mass = _reported_mass(source)
        if mass is None or mass != evidence.net_weight_g:
            reasons.add("package_mass_conflict_or_missing")
        try:
            staged_mass = (Decimal(str(row["reported_weight_g"]))
                           if row["reported_weight_g"] is not None else None)
        except InvalidOperation:
            staged_mass = None
        if staged_mass is None or staged_mass != evidence.net_weight_g:
            reasons.add("staging_mass_conflict_or_missing")
        protein, ambiguous = _provider_nutrient(source, "protein", "g")
        if ambiguous or protein is None:
            reasons.add("provider_protein_missing_or_ambiguous")
        elif protein != evidence.protein_per_100g:
            # Provider has not established a per-100g denominator. A disagreement
            # is a review signal, not a competing validated nutrition profile.
            reasons.add("provider_protein_disagrees_unverified_basis")
        kcal, ambiguous = _provider_nutrient(source, "energi_kcal", "kcal")
        if ambiguous:
            reasons.add("provider_kcal_ambiguous")
        elif kcal is not None and kcal != evidence.kcal_per_100g:
            if abs(kcal - evidence.kcal_per_100g) <= MAX_KCAL_WARNING:
                reasons.add("minor_kcal_difference_unverified_provider_basis")
            else:
                reasons.add("major_kcal_difference_needs_review")
        carbs, ambiguous = _provider_nutrient(source, "karbohydrater", "g")
        if ambiguous:
            reasons.add("provider_carbs_ambiguous")
        elif carbs is not None and carbs != evidence.carbs_per_100g:
            if abs(carbs - evidence.carbs_per_100g) <= MAX_CARBS_WARNING_G:
                reasons.add("minor_carbs_difference_unverified_provider_basis")
            else:
                reasons.add("major_carbs_difference_needs_review")
    blocking_identity = {
        "manual_manufacturer_review_pending", "no_matching_staging_product",
        "ean_conflict", "provider_ean_conflict", "variant_name_conflict",
        "provider_variant_name_conflict", "brand_conflict", "provider_brand_conflict",
        "package_mass_conflict_or_missing",
        "staging_mass_conflict_or_missing", "invalid_provider_json",
    }
    blocking_nutrition = {
        "provider_protein_missing_or_ambiguous",
        "provider_protein_disagrees_unverified_basis",
        "provider_kcal_ambiguous", "provider_carbs_ambiguous",
        "major_kcal_difference_needs_review",
        "major_carbs_difference_needs_review",
    }
    identity = "needs_review" if reasons & blocking_identity else "verified"
    nutrition = ("needs_review" if reasons & blocking_nutrition or not confirmed
                 else "verified_with_warning"
                 if any(x.startswith("minor_") for x in reasons) else "verified")
    overall = ("needs_review" if "needs_review" in (identity, nutrition)
               else "verified_with_warning" if nutrition == "verified_with_warning"
               else "verified")
    staging_ids = sorted(row["id"] for row in staging_rows)
    refs = [{"id": row["id"], "raw_payload_json": row["raw_payload_json"],
             "name": row["product_name"], "weight": row["reported_weight_g"]}
            for row in sorted(staging_rows, key=lambda x: x["id"])]
    review_hash = hashlib.sha256(json.dumps(
        {"manufacturer": evidence.raw_evidence_json, "staging": refs},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return Review(evidence=evidence, identity_status=identity,
                  nutrition_status=nutrition, overall_status=overall,
                  price_status=SOURCE_ONLY, reasons=tuple(sorted(reasons)),
                  candidate_count=len(staging_rows), review_sha256=review_hash,
                  staging_ids_json=json.dumps(staging_ids))


def review_private_db(db: sqlite3.Connection, evidence_list: list[ManufacturerEvidence]) -> list[Review]:
    db.row_factory = sqlite3.Row
    results = []
    for evidence in evidence_list:
        rows = db.execute(
            """SELECT id, ean, product_name, brand, reported_weight_g, raw_payload_json
               FROM kassalapp_staging WHERE ean=? ORDER BY id""",
            (evidence.ean,),
        ).fetchall()
        results.append(review(evidence, rows))
    return results
