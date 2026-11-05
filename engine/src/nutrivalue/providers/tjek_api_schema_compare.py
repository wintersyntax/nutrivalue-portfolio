"""TASK-033: offline-only Tjek v2 host schema comparison, count-only output.

One genuine API host may expose a different representation of an
underlying offer; that does not make two independent price observations.
Accepts already saved/private JSON dictionaries, never makes HTTP or
changes original offers. Does not assume a host exposes website-only
membership fields. A partial second-host response is only a sample.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from nutrivalue.providers.tjek_pdf_link import _normalized

# Exact observed names in Tjek website structured representation; their
# presence in either *v2 API response* has NOT yet been established.
OPTIONAL_SITE_KEYS = (
    "membershipPrice", "appPrice", "pieceCountGet", "pieceCountGetFor",
    "relativeSavings", "membershipRelativeSavings", "unitPrice",
    "baseUnit", "unitSizeFrom", "unitSizeTo", "pieceCountMin",
    "pieceCountMax", "publicId", "publicationPublicId",
    "businessPublicId",
)
API_IDENTITY_KEYS = ("id", "catalog_id", "dealer_id", "heading",
                     "run_from", "run_till", "pricing")


def _money(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        p = Decimal(str(value).replace(",", "."))
        cents = p * 100
        if (not p.is_finite() or p <= 0 or p > 100000
                or cents != cents.to_integral_value()):
            return None
        return int(cents)
    except (TypeError, ValueError, InvalidOperation):
        return None


def _utc(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.utcoffset() is not None else None
    except ValueError:
        return None


def _original_offers(payload: object, catalog_id: str) -> list[dict]:
    if not isinstance(payload, Mapping):
        raise ValueError("expected archived original Tjek envelope")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError("invalid original entries")
    original = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("invalid original entry")
        catalog = entry.get("catalog")
        if not isinstance(catalog, Mapping):
            raise ValueError("missing catalog")
        offers = entry.get("offers")
        if not isinstance(offers, list) or len(offers) > 1000:
            raise ValueError("invalid original offer list")
        if catalog.get("id") == catalog_id:
            original.extend(offers)
    if not 1 <= len(original) <= 1000:
        raise ValueError("no selected original catalog or too many offers")
    return original


def _other_offers(payload: object) -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping) and set(payload) <= {
        "offers", "source", "fetched_at"}:
        rows = payload.get("offers")
    else:
        raise ValueError("expected one saved Tjek v2 /offers JSON list")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("invalid bounded comparison offers")
    return rows


def _id_map(rows: list[dict], *, catalog_id: str,
            dealer_id: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid source offer")
        offer_id = row.get("id")
        if not isinstance(offer_id, str) or not offer_id or len(offer_id) > 100:
            raise ValueError("source offer must have original ID")
        if offer_id in result:
            raise ValueError("duplicate offer ID in one source")
        if row.get("catalog_id") != catalog_id:
            raise ValueError("missing/foreign comparison catalog ID")
        if row.get("dealer_id") != dealer_id:
            raise ValueError("missing/foreign retailer dealer ID")
        result[offer_id] = row
    return result


def _field_counts(rows: list[dict]) -> dict[str, int]:
    """Only known field-name counters, never raw data/ID/price fields."""
    counter = Counter()
    for row in rows:
        for name in OPTIONAL_SITE_KEYS:
            if name in row:
                counter["field_present:" + name] += 1
                if row[name] is not None:
                    counter["field_nonnull:" + name] += 1
        for name in API_IDENTITY_KEYS:
            if name in row:
                counter["api_field_present:" + name] += 1
    return dict(sorted(counter.items()))


def compare_api_views(original_payload: object, *,
                      catalog_id: str, dealer_id: str,
                      other_response: object | None = None) -> dict:
    """Compare archived Tjek against ONE previously saved other-host response.

    Does not prove API permissions, equality of upstream populations or
    independent prices. Numeric differences are reported only as counts.
    """
    if not (isinstance(catalog_id, str) and 2 <= len(catalog_id) <= 90
            and isinstance(dealer_id, str) and 2 <= len(dealer_id) <= 90):
        raise ValueError("exact catalog/dealer IDs required")
    original = _original_offers(original_payload, catalog_id)
    left = _id_map(original, catalog_id=catalog_id, dealer_id=dealer_id)
    out = {
        "state": "awaiting_permitted_other_host_response",
        "original_offers": len(left),
        "comparison_offers": 0,
        "id_overlap": 0,
        "exact_identity_compatible": 0,
        "heading_mismatch": 0,
        "pricing_ore_mismatch": 0,
        "validity_mismatch": 0,
        "original_unseen_in_comparison": None,
        "comparison_unseen_in_original": 0,
        "original_field_counts": _field_counts(original),
        "comparison_field_counts": {},
        "same_upstream_not_independent_price_confirmation": True,
        "rank_eligible": False,
        "http_requests": 0,
        "source_only": True,
    }
    if other_response is None:
        return out
    second = _other_offers(other_response)
    right = _id_map(second, catalog_id=catalog_id, dealer_id=dealer_id)
    overlap = set(left) & set(right)
    out["state"] = "compared_partial_or_complete_saved_response"
    out["comparison_offers"] = len(right)
    out["id_overlap"] = len(overlap)
    out["comparison_unseen_in_original"] = len(set(right) - set(left))
    # This is a sample of the second host unless the caller independently
    # attests exhaustive pagination. Never infer offers are missing.
    out["original_unseen_in_comparison"] = len(set(left) - set(right))
    out["comparison_field_counts"] = _field_counts(second)
    for offer_id in overlap:
        a, b = left[offer_id], right[offer_id]
        if not all(isinstance(row.get("heading"), str) and row["heading"]
                   for row in (a, b)):
            out["heading_mismatch"] += 1
            continue
        if _normalized(a["heading"]) != _normalized(b["heading"]):
            out["heading_mismatch"] += 1
            continue
        ap, bp = a.get("pricing"), b.get("pricing")
        if (not isinstance(ap, Mapping) or not isinstance(bp, Mapping)
                or ap.get("currency") != "NOK" or bp.get("currency") != "NOK"
                or _money(ap.get("price")) is None
                or _money(bp.get("price")) is None
                or _money(ap.get("price")) != _money(bp.get("price"))):
            out["pricing_ore_mismatch"] += 1
            continue
        dates = [
            (_utc(a.get(key)), _utc(b.get(key)))
            for key in ("run_from", "run_till")
        ]
        if any(x is None or y is None or x != y for x, y in dates):
            out["validity_mismatch"] += 1
            continue
        out["exact_identity_compatible"] += 1
    return out
