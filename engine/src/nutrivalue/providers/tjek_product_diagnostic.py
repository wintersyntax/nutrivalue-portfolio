"""TASK-034A: read-only schema/candidate counts, never product matching.

Structural GTIN candidates have ASCII digits and supported string lengths;
no GS1 check digit, canonicalization, nutrition join or price promotion here.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import time

from nutrivalue.providers.tjek_app_cache import (
    AppCacheClient, AppCacheError, MAX_BATCH_OFFERS, strict_json,
)
from nutrivalue.providers.tjek_offline import CHAINS
from nutrivalue.providers.tjek_site_structured import extract_structured_offers, MAX_HTML_BYTES
from nutrivalue.providers.tjek_weekly_collect import _safe_id

MAX_OFFERS = 30
MAX_NODES = 10000
MAX_DEPTH = 20
KNOWN_FIELDS = frozenset(("ean", "ean8", "ean13", "gtin", "gtin8", "gtin12",
                          "gtin13", "gtin14", "barcode", "barcodes", "upc",
                          "productcode", "identifiers"))
SENSITIVE = re.compile(r"secret|token|password|authorization|cookie|api.?key", re.I)
SEMANTIC = re.compile(r"(?:ean(?:8|13)?|gtin(?:8|12|13|14)?|barcodes?|upc)", re.I)
UNEXPECTED = re.compile(r"(?:ean(?:8|13)?|gtin(?:8|12|13|14)?|barcodes?|upc|identifiers?|code)", re.I)
CLASSES = ("packaged_branded", "fresh_generic", "unknown")
COUNTERS = ("offers_tested", "offer_success", "offerProducts_success",
            "offerProducts_empty", "offerProducts_nonempty", "product_objects_total",
            "gtin_like_candidates", "structurally_valid_gtin_candidates")


def sample_specs(samples):
    """Bound explicit owner labels and deduplicate IDs without guessing classes."""
    if not isinstance(samples, list) or not 0 < len(samples) <= 60:
        raise AppCacheError("invalid_samples")
    by_id = {}
    for sample in samples:
        if (not isinstance(sample, dict) or
                set(sample) - {"publicId", "retailer", "product_class"}):
            raise AppCacheError("invalid_samples")
        try:
            oid = _safe_id(sample.get("publicId"))
        except (ValueError, TypeError):
            raise AppCacheError("invalid_samples") from None
        retailer = sample.get("retailer", "unknown")
        product_class = sample.get("product_class", "unknown")
        if retailer not in (*CHAINS.values(), "unknown") or product_class not in CLASSES:
            raise AppCacheError("invalid_sample_labels")
        normalized = {"publicId": oid, "retailer": retailer, "product_class": product_class}
        if oid in by_id and by_id[oid] != normalized:
            raise AppCacheError("conflicting_sample_labels")
        by_id[oid] = normalized
    if len(by_id) > MAX_OFFERS:
        raise AppCacheError("sample_limit")
    return list(by_id.values())


def _kind(value):
    if value is None:
        return "null"
    return {str: "string", bool: "boolean", int: "integer", float: "number",
            dict: "object", list: "array"}.get(type(value), "unknown")


def survey_products(products):
    """Inspect bounded unknown schemas; keep values out of the returned report.

    Generic productCode/ID/SKU values are never GTIN evidence. Explicit
    typed identifier records and unexpectedly named GTIN fields are surveyed.
    Sensitive subtrees are omitted; dynamic key text is replaced with a label.
    """
    if (not isinstance(products, list) or len(products) > 500
            or any(not isinstance(product, dict) for product in products)):
        raise AppCacheError("invalid_products")
    fields, unexpected, schema = Counter(), Counter(), {}
    like = structural = visited = 0
    stack = [(p, "products[]", 0, False, False) for p in products]
    while stack:
        value, path, depth, candidate, identifier_map = stack.pop()
        visited += 1
        if visited > MAX_NODES or depth > MAX_DEPTH:
            raise AppCacheError("schema_budget")
        if isinstance(value, dict):
            if len(value) + visited + len(stack) > MAX_NODES:
                raise AppCacheError("schema_budget")
            types = [value[name] for name in ("type", "scheme") if name in value]
            typed_gtin = bool(types) and all(isinstance(typed, str) and re.fullmatch(
                r"(?:ean(?:8|13)?|gtin(?:8|12|13|14)?|upc|barcode)", typed, re.I)
                for typed in types)
            # Explicit SKU/unknown/conflicting typing defeats inherited context.
            wrapper_gtin = typed_gtin if types else candidate
            for name, child in value.items():
                if not isinstance(name, str):
                    raise AppCacheError("invalid_products")
                sensitive = SENSITIVE.search(name) is not None
                normalized = name.lower().replace("_", "").replace("-", "")
                static_identifier_key = (normalized in KNOWN_FIELDS or
                                         name in ("type", "scheme", "value", "code", "id", "name", "format", "kind"))
                dynamic = identifier_map and not static_identifier_key
                safe_name = ("<redacted>" if sensitive else name
                             if not dynamic and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name)
                             else "<dynamic>")
                child_path = path + "." + safe_name
                schema.setdefault(child_path, Counter())[_kind(child)] += 1
                if sensitive:
                    continue
                # Match field-name tokens, not incidental letters in "clean".
                tokens = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", safe_name).split("_")
                tokens = [token for part in tokens for token in part.split("-")]
                if normalized in KNOWN_FIELDS:
                    fields[safe_name] += 1
                elif any(UNEXPECTED.fullmatch(token) for token in tokens):
                    unexpected[child_path] += 1
                explicit_gtin = normalized in KNOWN_FIELDS - {"identifiers", "productcode"}
                semantic = (not dynamic and (explicit_gtin or
                            any(SEMANTIC.fullmatch(token) for token in tokens) or
                            (name == "value" and wrapper_gtin)))
                child_map = (normalized == "identifiers" or semantic or
                             (identifier_map and not static_identifier_key))
                stack.append((child, child_path, depth + 1, semantic, child_map))
        elif isinstance(value, list):
            if len(value) + visited > MAX_NODES:
                raise AppCacheError("schema_budget")
            stack.extend((item, path + "[]", depth + 1, candidate, identifier_map) for item in value)
        elif candidate and (isinstance(value, str) or type(value) is int):
            digits = str(value)
            if re.fullmatch(r"[0-9]+", digits):
                like += 1
                if isinstance(value, str) and len(value) in (8, 12, 13, 14):
                    structural += 1
    return {
        "product_objects_total": len(products),
        "identifier_field_counts": dict(sorted(fields.items())),
        "unexpected_identifier_fields": dict(sorted(unexpected.items())),
        "schema_fields": {path: dict(sorted(types.items())) for path, types in sorted(schema.items())},
        "gtin_like_candidates": like,
        "structurally_valid_gtin_candidates": structural,
        "gs1_check_digit_validated": False,
    }


def _private_path(path, private_root, suffix, cap):
    try:
        path, root = Path(path), Path(private_root)
        if root.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError
        resolved, resolved_root = path.resolve(strict=True), root.resolve(strict=True)
        resolved.relative_to(resolved_root)
        # Inspect only the private boundary; macOS /var itself is a normal
        # system symlink outside that boundary. Never follow an input symlink.
        relative = path.absolute().relative_to(root.absolute())
        if ".." in relative.parts:
            raise ValueError
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError
        if resolved.suffix != suffix or not 0 < resolved.stat().st_size <= cap:
            raise ValueError
        with resolved.open("rb") as handle:
            raw = handle.read(cap + 1)
        if len(raw) > cap:
            raise ValueError
        return raw
    except (ValueError, TypeError, OSError):
        raise AppCacheError("invalid_private_input") from None


def read_sample_file(path, *, private_root):
    return sample_specs(strict_json(_private_path(path, private_root, ".json", 128000)))


def html_samples(path, *, expected_sha256, private_root):
    """Reuse TASK-033 extraction from one private, SHA-gated saved page."""
    raw = _private_path(path, private_root, ".html", MAX_HTML_BYTES)
    if (not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise AppCacheError("html_digest_mismatch")
    try:
        rows = extract_structured_offers(raw)
    except (ValueError, RecursionError):
        raise AppCacheError("unsupported_saved_html") from None
    samples = [{"publicId": row["publicId"],
                "retailer": CHAINS.get(row.get("businessPublicId"), "unknown"),
                "product_class": "unknown"} for row in rows[:MAX_OFFERS]]
    return sample_specs(samples)


def diagnose(samples, *, live=False, client=None, sleeper=time.sleep):
    """At most 30 distinct offers, six serial POSTs and one second pacing.

    A transport/protocol failure stops the run. Individual cache errors
    remain countable and never turn null, absent or invalid values into [].
    Group labels come from the input manifest, not verified SKU classification.
    """
    samples = sample_specs(samples)
    report = {name: 0 for name in COUNTERS}
    report.update(status="offline_plan", offers_requested=len(samples),
                  offers_not_tested=len(samples), app_cache_requests=0,
                  identifier_field_counts={}, unexpected_identifier_fields={}, schema_fields={},
                  errors_by_type={}, results_by_retailer={}, results_by_product_class={},
                  product_evidence="not_tested", gs1_check_digit_validated=False,
                  source_only=True, rank_eligible=False,
                  source_family="tjek", source_endpoint="https://etilbudsavis.no/", observed_at=None,
                  grouping_basis="input_sample_labels_unverified",
                  publication_viewer_status="unverified_not_called")
    if live is not True:
        return report
    if client is None:
        try:
            client = AppCacheClient.from_environment(live=True)
        except AppCacheError:
            report["status"] = "skipped_missing_credentials"
            return report
    report["observed_at"] = datetime.now(timezone.utc).isoformat()
    started = client.requests
    errors = Counter()
    report["status"] = "complete_sample"

    def increment(sample, name, count=1):
        report[name] += count
        for grouping, label in (("results_by_retailer", sample["retailer"]),
                                ("results_by_product_class", sample["product_class"])):
            group = report[grouping].setdefault(label, {field: 0 for field in COUNTERS})
            group[name] += count

    for offset in range(0, len(samples), MAX_BATCH_OFFERS):
        batch = samples[offset:offset + MAX_BATCH_OFFERS]
        before = client.requests
        try:
            results = client.fetch_offers([sample["publicId"] for sample in batch])
        except AppCacheError as error:
            if client.requests > before:
                for sample in batch:
                    increment(sample, "offers_tested")
            errors[error.code] += 1
            break
        for sample in batch:
            increment(sample, "offers_tested")
            oid = sample["publicId"]
            for kind in ("offer", "offerProducts"):
                result = results[kind, oid]
                if result["status"] != "success":
                    errors[result["error"]] += 1
                    continue
                increment(sample, kind + "_success")
                if kind == "offer":
                    continue
                products = result["value"]
                increment(sample, "offerProducts_nonempty" if products else "offerProducts_empty")
                try:
                    survey = survey_products(products)
                except AppCacheError as error:
                    # Do not misreport observed objects as zero when survey bounds fail.
                    increment(sample, "product_objects_total", len(products))
                    errors[error.code] += 1
                    continue
                for name in ("product_objects_total", "gtin_like_candidates", "structurally_valid_gtin_candidates"):
                    increment(sample, name, survey[name])
                for name in ("identifier_field_counts", "unexpected_identifier_fields"):
                    counts = Counter(report[name])
                    counts.update(survey[name])
                    report[name] = dict(sorted(counts.items()))
                for path, types in survey["schema_fields"].items():
                    counts = Counter(report["schema_fields"].get(path, {}))
                    counts.update(types)
                    report["schema_fields"][path] = dict(sorted(counts.items()))
        if offset + MAX_BATCH_OFFERS < len(samples):
            sleeper(1.0)
    report["app_cache_requests"] = client.requests - started
    report["offers_not_tested"] = len(samples) - report["offers_tested"]
    report["errors_by_type"] = dict(sorted(errors.items()))
    report["schema_fields"] = dict(sorted(report["schema_fields"].items()))
    if errors or report["offers_not_tested"]:
        report["status"] = "partial_or_error"
    report["product_evidence"] = (
        "nonempty_offerProducts_observed" if report["offerProducts_nonempty"] else
        "no_nonempty_offerProducts_observed" if report["offerProducts_success"] else
        "no_successful_product_response")
    return report
