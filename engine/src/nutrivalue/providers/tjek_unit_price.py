"""TASK-033: offline review-only per-kg/l arithmetic on archived Tjek offers.

Adapted the safety ideas, not code, from donadelicc/kundeavis-mcp.
A printed value inside a Tjek description is NOT independent publisher
PDF corroboration. Never assert local-store checkout/rank eligibility.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Mapping

from nutrivalue.providers.tjek_offer_conditions import interpret_offer

UNITS = {
    "kg": ("kg", Decimal("1")), "g": ("kg", Decimal("0.001")),
    "hg": ("kg", Decimal("0.1")), "l": ("l", Decimal("1")),
    "dl": ("l", Decimal("0.1")), "cl": ("l", Decimal("0.01")),
    "ml": ("l", Decimal("0.001")),
}
NUM = r"\d{1,5}(?:[.,]\d{1,2})?(?![\d.,])"
AFTER = re.compile(
    rf"\b(?:pr|per)\.?\s*(?:(100)\s*)?(kg|hg|g|l|dl|cl|ml)\b"
    rf"\s*[:.]?\s*({NUM})(?!\s*[x×])", re.I)
SUFFIX = re.compile(rf"(?<![\w])({NUM})\s*/\s*(kg|hg|g|l|dl|cl|ml)\b", re.I)

# Some REMA labels put the unit price BEFORE 'pr kg'/'pr l':
# '158,64 pr. kg 34,90 pr. stk'. The final 34,90 is a PACK price,
# not a rate for kg. Suppress the AFTER rule at those marker offsets.
BEFORE = re.compile(
    rf"(?<![\w.,])(?P<value>{NUM})[ \t]+"
    rf"(?P<unit_marker>(?:pr|per)\.?[ \t]*(?:100[ \t]*)?"
    rf"(?:kg|hg|g|l|dl|cl|ml)\b)", re.I)
BEFORE_UNIT = re.compile(
    r"(?:pr|per)\.?[ \t]*(?:(100)[ \t]*)?(kg|hg|g|l|dl|cl|ml)\b", re.I)

# Retail descriptions may quote an *ordinary/reference* unit price rather
# than the promotional price (e.g. 'Ord. pris 18,50 pr. hg').
REFERENCE_PREFIX = re.compile(
    rf"\b(?:ord\.?\s*pris|ordinær\s*pris|normalpris|førpris|før)"
    rf"\s*(?:kr\.?\s*)?(?:{NUM}\s*)?$", re.I)
REFERENCE_UNIT = re.compile(
    rf"\b(?:ord\.?\s*pris|ordinær\s*pris|normalpris|førpris|før)"
    rf"\s*(?:kr\.?\s*)?{NUM}\s*"
    r"(?:(?:pr|per)\.?\s*(?:100\s*)?(?:kg|hg|g|l|dl|cl|ml)\b"
    r"|/\s*(?:kg|hg|g|l|dl|cl|ml)\b)", re.I)
# '39,87-53,20 pr. kg': a rate range, NOT '53,20 pr. kg' exact.
RANGE_BEFORE = re.compile(
    rf"(?<![\w.,])(?P<low>{NUM})[ \t]*[-–—][ \t]*(?P<high>{NUM})"
    rf"[ \t]+(?P<unit_marker>(?:pr|per)\.?[ \t]*(?:100[ \t]*)?"
    rf"(?:kg|hg|g|l|dl|cl|ml)\b)", re.I)
# 'Fra 80 g' is a *minimum* pack mass even when Tjek quantity says 80g.
MIN_PACK_IN_TEXT = re.compile(
    rf"\bfra[ \t]+(?:ca\.?[ \t]+)?(?P<amount>{NUM})[ \t]*"
    r"(?P<unit>kg|hg|g|l|dl|cl|ml)\b", re.I)



def _number(v: object) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        n = Decimal(str(v).strip().replace(",", "."))
        return n if n.is_finite() and 0 < n <= 100000 else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def _price_ore(v: object) -> int | None:
    n = _number(v)
    return (int(n * 100) if n is not None and
            n * 100 == (n * 100).to_integral_value() else None)


def _quantity(q: object) -> tuple[str | None, Decimal | None, str]:
    if not isinstance(q, Mapping):
        return None, None, "missing_quantity"
    unit, size, pieces = q.get("unit"), q.get("size"), q.get("pieces")
    if not isinstance(unit, Mapping) or not isinstance(size, Mapping):
        return None, None, "missing_size_or_unit"
    symbol = unit.get("symbol")
    if not isinstance(symbol, str) or symbol.casefold() not in UNITS:
        return None, None, "unsupported_unit"
    basis, factor = UNITS[symbol.casefold()]
    si = unit.get("si")
    if isinstance(si, Mapping) and si.get("symbol") is not None:
        if str(si["symbol"]).lower() != basis or _number(si.get("factor")) != factor:
            return basis, None, "si_unit_conflict"
    size_from = _number(size.get("from"))
    size_to = _number(size.get("to")) if size.get("to") is not None else size_from
    if size_from is None or size_to is None:
        return basis, None, "invalid_pack_size"
    if size_from != size_to:
        return basis, None, "pack_size_range"
    if pieces is not None and not isinstance(pieces, Mapping):
        return basis, None, "invalid_pieces"
    count = (_number(pieces["from"]) if pieces and pieces.get("from") is not None
             else Decimal(1))
    count_to = (_number(pieces["to"]) if pieces and pieces.get("to") is not None
                else count)
    if count is None or count_to is None or count != count_to:
        return basis, None, "pieces_range_or_invalid"
    total = size_from * factor * count
    if not 0 < total <= Decimal("100000"):
        return basis, None, "invalid_total_quantity"
    return basis, total, "ok"


def _printed(description: str) -> list[tuple[str, int, bool]]:
    """Parse kr/kg/l figures attached to this same original offer's text.

    'Fra' is a lower bound, not proof of a particular SKU's price.
    Reject historical-price labels and numeric pack multipliers.
    """
    found: set[tuple[str, int, bool]] = set()
    # Inspect BEFORE/AFTER/SUFFIX separately so 'X pr. kg Y pr. stk'
    # does not attach pack price Y to the kg marker. When two amounts
    # precede one marker, preserve both as non-exact range evidence.
    ranges = list(RANGE_BEFORE.finditer(description))
    range_markers = {m.start("unit_marker") for m in ranges}
    for span in ranges:
        prev = description[max(0, span.start() - 32):span.start()].casefold()
        if REFERENCE_PREFIX.search(prev):
            continue
        marker = BEFORE_UNIT.fullmatch(span.group("unit_marker"))
        if marker is None:
            continue
        per100, unit = marker.groups()
        basis, factor = UNITS[unit.lower()]
        multiplier = Decimal(100) if per100 else Decimal(1)
        for name in ("low", "high"):
            amount = _number(span.group(name))
            if amount is None:
                continue
            normalized = amount / (factor * multiplier) * 100
            if normalized > Decimal("10000000"):
                continue
            found.add((
                basis, int(normalized.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                True,  # not an exact unit rate for a specific package
            ))
    before_matches = [
        m for m in BEFORE.finditer(description)
        if m.start("unit_marker") not in range_markers
    ]
    before_markers = range_markers | {
        m.start("unit_marker") for m in before_matches
    }
    for matcher in (BEFORE, AFTER, SUFFIX):
        matches = before_matches if matcher is BEFORE else matcher.finditer(description)
        for m in matches:
            if matcher is BEFORE:
                raw = m.group("value")
                marker = BEFORE_UNIT.fullmatch(m.group("unit_marker"))
                if marker is None:
                    continue
                per100, unit = marker.groups()
                count = Decimal(100) if per100 else Decimal(1)
            elif matcher is AFTER:
                if m.start() in before_markers:
                    continue
                per100, unit, raw = m.groups()
                count = Decimal(100) if per100 else Decimal(1)
            else:
                raw, unit = m.groups()
                count = Decimal(1)
            prev = description[max(0, m.start() - 30):m.start()].casefold()
            tail = description[m.end():m.end() + 10]
            if REFERENCE_PREFIX.search(prev):
                continue
            if re.match(r"\s*(?:%|[x×]\s*(?:\d|ca\.?\s*\d)|/[a-z])", tail, re.I):
                continue
            if re.match(r"\s*[-/–]\s*\d", tail):
                # This printed unit price spans variants: do not treat the
                # first endpoint as one uniquely corroborated product rate.
                trailing_range = True
            else:
                trailing_range = False
            num = _number(raw)
            basis, factor = UNITS[unit.lower()]
            if num is None:
                continue
            rate = num / (count * factor) * 100
            if rate > Decimal("10000000"):
                continue
            lower = trailing_range or bool(re.search(r"\bfra\s*$", prev))
            found.add((basis, int(rate.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), lower))
    return sorted(found)


def interpret_unit_price(offer: Mapping, *,
                         bundle_units: int | None = None,
                         pdf_bundle_total_correlated: bool = False) -> dict:
    """Return exact-number candidates separately from their evidence status.

    Bundle units must come from original matching API text or an independent
    page+title+badge PDF correlation, not an assumed quantity.
    """
    if not isinstance(offer, Mapping):
        raise ValueError("offer must be an object")
    pricing = offer.get("pricing")
    if not isinstance(pricing, Mapping) or pricing.get("currency") != "NOK":
        raise ValueError("NOK pricing required")
    text = offer.get("description") or ""
    if not isinstance(text, str):
        raise ValueError("description must be text")
    source = interpret_offer(offer)
    b, qty, status = _quantity(offer.get("quantity"))
    price = _price_ore(pricing.get("price"))
    api_bundle = source["bundle_units"]
    if pdf_bundle_total_correlated and (
            type(bundle_units) is not int or not 2 <= bundle_units <= 12):
        raise ValueError("PDF bundle quantity not corroborated")
    if bundle_units is not None and not pdf_bundle_total_correlated:
        raise ValueError("bundle units without PDF corroboration")
    if api_bundle is not None and bundle_units is not None and api_bundle != bundle_units:
        raise ValueError("API/PDF bundle quantity conflict")
    units = api_bundle or (bundle_units if pdf_bundle_total_correlated else None)
    printed = _printed(text)
    same = [(n, lower) for dimension, n, lower in printed if dimension == b]
    result = {
        "basis": b, "pack_quantity_basis_units": str(qty) if qty else None,
        "bundle_units": units, "computed_ore_per_basis_review_only": None,
        "state": "unavailable", "source_printed_candidates": len(same),
        "printed_origin": "tjek_description_not_independent_pdf",
        "source_status": "source_only", "rank_eligible": False,
        "checkout_verified": False,
    }
    if status != "ok":
        result["state"] = status
        return result
    # Tjek's exact quantity fields may hold the *minimum* package size
    # of a group promotion. A single kg/l rate cannot be determined.
    minimum_pack = any(
        UNITS[m.group("unit").casefold()][0] == b
        for m in MIN_PACK_IN_TEXT.finditer(text)
    )
    if minimum_pack:
        result["state"] = "pack_size_minimum_in_description"
        result["pack_quantity_basis_units"] = None
        return result
    if price is None or qty is None:
        result["state"] = "missing_or_invalid_price"
        return result
    if api_bundle is not None and source["bundle_total_ore"] != price:
        result["state"] = "conflicting_api_bundle_total"
        return result
    divisor = qty * Decimal(units or 1)
    rate = Decimal(price) / divisor
    if not 0 < rate <= Decimal("10000000"):
        result["state"] = "invalid_unit_rate"
        return result
    result["computed_ore_per_basis_review_only"] = int(
        rate.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if units is not None:
        result["state"] = "conditional_bundle_rate_review_only"
        return result
    if not same:
        result["state"] = (
            "ordinary_or_previous_reference_unit_only"
            if REFERENCE_UNIT.search(text)
            else "no_comparable_printed_unit_price"
        )
        return result
    # Ranges / 'Fra' and different price tiers can be real but cannot
    # independently verify one specific package without its conditions.
    exact = {n for n, lower in same if not lower}
    if len(exact) != 1 or len(same) != 1:
        result["state"] = "printed_range_or_lower_bound_review"
        return result
    printed_ore = next(iter(exact))
    tolerance = max(2, int(Decimal(printed_ore) * Decimal("0.005")))
    if abs(result["computed_ore_per_basis_review_only"] - printed_ore) > tolerance:
        result["state"] = ("mixed_member_tiers_review"
                           if source["explicit_nonmember_ore"] is not None
                           or source["explicit_member_ore"] is not None
                           else "unexplained_unit_rate_mismatch")
        return result
    result["state"] = "source_text_unit_rate_consistent_review_only"
    return result



def condition_unit_prices_from_pdf_bundles(
        offers: list[Mapping], badge_rows: list[Mapping]) -> list[dict]:
    """Join authoritative-input order, matching IDs/pages, before rate analysis.

    Caller MUST supply badge_rows directly from the validated, same-PDF
    match_multibuy_badges result. This helper does not itself certify a
    retailer, total, branded SKU, PDF page or checkout promotion.
    The association never alters the original API source or makes a
    conditionally derived rate rank eligible.
    """
    if len(offers) != len(badge_rows):
        raise ValueError("source offers / PDF badge evidence counts differ")
    result = []
    ids = set()
    for offer, proof in zip(offers, badge_rows):
        if not isinstance(offer, Mapping) or not isinstance(proof, Mapping):
            raise ValueError("invalid source/geometry evidence")
        oid, page = offer.get("id"), offer.get("catalog_page")
        if (not isinstance(oid, str) or not oid or oid in ids
                or type(page) is not int or
                proof.get("source_offer_id") != oid or
                proof.get("source_catalog_page") != page):
            raise ValueError("PDF geometry from foreign/duplicate offer or page")
        ids.add(oid)
        prior = interpret_unit_price(offer)
        accepted = proof.get("badge_state") == "geometry_total_correlated_review_only"
        if accepted:
            if (proof.get("source_total_recorroborated") is not True
                    or proof.get("source_status") != "source_only"
                    or proof.get("rank_eligible") is not False):
                raise ValueError("uncorroborated or non-review-only PDF bundle evidence")
            units = proof.get("bundle_units_candidate")
            after = interpret_unit_price(
                offer, bundle_units=units, pdf_bundle_total_correlated=True)
            if after["bundle_units"] != units:
                raise ValueError("PDF banner unit count conflicts with source")
        else:
            if proof.get("source_total_recorroborated") is True:
                raise ValueError("total corroboration flag without accepted badge")
            after = prior
        result.append({
            "source_offer_id": oid,
            "source_catalog_page": page,
            "pdf_condition_correlated": accepted,
            "new_pdf_bundle_condition": accepted and prior["bundle_units"] is None,
            "state_before": prior["state"],
            "state_after": after["state"],
            "basis": after["basis"],
            "review_only_unit_ore": after["computed_ore_per_basis_review_only"],
            "source_status": "source_only",
            "rank_eligible": False,
        })
    return result


def pdf_bundle_unit_summary(rows: list[Mapping]) -> dict[str, int]:
    """Aggregate-only: neither item identities nor private numerical rates."""
    correlated = [row for row in rows if row["pdf_condition_correlated"]]
    return {
        "pdf_bundle_conditions_correlated_review_only": len(correlated),
        "pdf_bundle_conditions_new_to_api_text":
            sum(row["new_pdf_bundle_condition"] for row in correlated),
        "pdf_bundle_unit_rates_computable_review_only":
            sum(row["review_only_unit_ore"] is not None for row in correlated),
        "pdf_bundle_unit_rates_unavailable":
            sum(row["review_only_unit_ore"] is None for row in correlated),
        "formerly_api_source_consistent_but_bundle_total":
            sum(row["state_before"] == "source_text_unit_rate_consistent_review_only"
                for row in correlated),
        "formerly_unexplained_mismatch_now_bundle_condition":
            sum(row["state_before"] == "unexplained_unit_rate_mismatch"
                for row in correlated),
        "new_verified_shop_unit_prices": 0,
        "new_rank_eligible_prices": 0,
    }



def mismatch_clue_bucket(offer: Mapping, assessment: Mapping) -> str:
    """Unidentified REVIEW-ONLY discrepancy pattern, never an explanation.

    Return only coarse category labels, no IDs/product names or NOK values.
    A near-2x ratio may be a hidden bundle, a different pack, or an error.
    """
    if assessment.get("state") != "unexplained_unit_rate_mismatch":
        return "not_an_unexplained_mismatch"
    dimension = assessment.get("basis")
    our_ore = assessment.get("computed_ore_per_basis_review_only")
    if type(our_ore) is not int or our_ore <= 0:
        return "unable_to_compare_ratio"
    text = offer.get("description") or ""
    if not isinstance(text, str):
        return "unable_to_compare_ratio"
    matched = [value for b, value, lower in _printed(text)
               if b == dimension and not lower]
    if len(matched) != 1 or matched[0] <= 0:
        return "unable_to_compare_ratio"
    ratio = Decimal(our_ore) / Decimal(matched[0])
    if abs(ratio - Decimal(2)) <= Decimal("0.04"):
        return "computed_about_twice_printed_possible_bundle_or_pack"
    if abs(ratio - Decimal(3)) <= Decimal("0.06"):
        return "computed_about_three_times_printed_possible_bundle_or_pack"
    if abs(ratio - Decimal("0.5")) <= Decimal("0.01"):
        return "computed_about_half_printed_possible_variant_or_pack"
    if Decimal("0.98") <= ratio <= Decimal("1.02"):
        return "small_gap_above_half_percent_review"
    return "other_unexplained_difference"
