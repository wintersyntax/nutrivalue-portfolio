"""TASK-033: Conservative, OFFLINE interpretation of original Tjek offer text.

Derived values are source-only interpretations, NOT shelf/checkout verification.
No HTTP, database mutations, EAN assumptions, or promotion of unverified prices.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Mapping

AMOUNT = r"\d{1,6}(?:[.,]\d{1,2})?(?:,-)?"
NONMEMBER = re.compile(
    r"\bikke[\s-]*medlem(?:spris)?\s*:\s*(?:kr\.?\s*)?(" + AMOUNT + r")", re.I)
MEMBER_PRICE = re.compile(
    r"\bmedlems?[\s-]*pris\s*:?\s*(?:kr\.?\s*)?(" + AMOUNT + r")", re.I)
SAVING = re.compile(
    r"\bspar\s*(?:kr\.?\s*|:\s*)?(" + AMOUNT + r")", re.I)
BEFORE = re.compile(
    r"\bfør(?:pris)?\s*:?\s*(?:kr\.?\s*)?(" + AMOUNT + r")", re.I)
BUNDLE = re.compile(
    r"\b([2-9]|1\d)\s*(?:stk\.?\s*)?for\s*(" + AMOUNT + r")", re.I)
PERCENT = re.compile(r"\b(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
MEMBER_CUE = re.compile(r"\bmedlems?[\s-]*(?:kupp|tilbud|pris)\b", re.I)
DEPOSIT = re.compile(r"\bpant\b", re.I)


def _ore(value: object) -> int | None:
    """Integer øre without binary-float multiplication or 199,- ambiguity."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        normalized = str(value).strip().replace(",", ".")
        if normalized.endswith(".-"):
            normalized = normalized[:-2]
        amount = Decimal(normalized)
        cents = amount * 100
        if (not amount.is_finite() or not Decimal("0") < amount <= Decimal("1000000")
                or cents != cents.to_integral_value()):
            return None
        return int(cents)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _unique_amount(text: str, pattern: re.Pattern, group: int = 1):
    found = [_ore(match.group(group)) for match in pattern.finditer(text)]
    found = [amount for amount in found if amount is not None]
    return (found[0] if len(found) == 1 else None, len(found))


def interpret_offer(offer: Mapping[str, object]) -> dict:
    """Interpret one ORIGINAL Tjek offer without replacing its original fields.

    The result separates safely advertisable conditions from review-only
    candidate amounts. Price rank eligibility remains outside TASK-033.
    """
    if not isinstance(offer, Mapping):
        raise ValueError("offer must be an object")
    pricing = offer.get("pricing")
    if not isinstance(pricing, Mapping) or pricing.get("currency") != "NOK":
        raise ValueError("NOK pricing object required")
    description = offer.get("description")
    heading = offer.get("heading")
    if (description is not None and not isinstance(description, str)
            or not isinstance(heading, str)):
        raise ValueError("malformed offer text")
    text = f"{heading} {description or ''}"
    source = _ore(pricing.get("price"))
    previous = _ore(pricing.get("pre_price"))
    nonmember, nonmember_n = _unique_amount(text, NONMEMBER)
    member, member_n = _unique_amount(text, MEMBER_PRICE)
    saving, saving_n = _unique_amount(text, SAVING)
    before, before_n = _unique_amount(text, BEFORE)
    bundles = [(int(m.group(1)), _ore(m.group(2))) for m in BUNDLE.finditer(text)]
    percentages = []
    for match in PERCENT.finditer(text):
        surrounding = text[max(0, match.start() - 30):match.end() + 40].casefold()
        # Avoid descriptions such as "100 % ull".
        if re.search(r"rabatt|spar|avslag|pris|på (?:alle|utvalgte|varer)", surrounding):
            percentages.append(match.group(1).replace(",", "."))
    flags = []
    if nonmember_n > 1 or member_n > 1 or saving_n > 1 or before_n > 1 or len(bundles) > 1:
        flags.append("ambiguous_multiple_conditions")
    member_cue = bool(MEMBER_CUE.search(text))
    saving_difference = (
        nonmember - saving
        if nonmember is not None and saving is not None and 0 < saving < nonmember
        else None
    )
    if saving is not None and nonmember is not None and saving >= nonmember:
        flags.append("invalid_nonmember_saving")
    if member is not None and nonmember is not None and member >= nonmember:
        flags.append("member_not_below_nonmember")
    if member is not None and saving_difference is not None and member != saving_difference:
        flags.append("explicit_member_saving_conflict")
    if (nonmember is not None and source is not None
            and source not in {nonmember, member, saving_difference}):
        flags.append("source_nonmember_mismatch")

    bundle_units = bundle_total = bundle_unit_ore = None
    if len(bundles) == 1:
        bundle_units, bundle_total = bundles[0]
        if bundle_total is None:
            flags.append("invalid_bundle_total")
        else:
            if bundle_total % bundle_units == 0:
                bundle_unit_ore = bundle_total // bundle_units
            if source is not None and source != bundle_total:
                flags.append("source_bundle_mismatch")
    if percentages:
        flags.append("percentage_needs_item_base_and_scope")
    has_pant = bool(DEPOSIT.search(text))
    if has_pant:
        flags.append("pant_checkout_total_not_verified")

    possible_member = None
    confirmed_member = None
    member_evidence = "none"
    conflict = bool(set(flags) & {
        "ambiguous_multiple_conditions", "invalid_nonmember_saving",
        "source_nonmember_mismatch", "member_not_below_nonmember",
        "explicit_member_saving_conflict", "source_bundle_mismatch",
    })
    if member is not None and not conflict:
        confirmed_member = member
        member_evidence = "explicit_text_member_price"
    elif (saving_difference is not None and nonmember is not None
          and source in {nonmember, saving_difference} and not conflict):
        possible_member = saving_difference
        if member_cue:
            confirmed_member = saving_difference
            member_evidence = (
                "member_cue_saving_already_in_source" if source == saving_difference
                else "member_cue_and_saving_arithmetic"
            )
        else:
            member_evidence = "candidate_nonmember_minus_saving_review_only"
            flags.append("member_eligibility_not_explicit")
    # Bundles are conditional minimum-basket offers, not one-item prices.
    if bundle_units is not None:
        if confirmed_member is not None or possible_member is not None:
            flags.append("bundle_member_combination_needs_review")
        confirmed_member = None
        possible_member = None

    price_kind = "unknown_source_price"
    if source is not None and bundle_total == source and not conflict:
        price_kind = "advertised_bundle_total"
    elif source is not None and member == source and not conflict:
        price_kind = "advertised_member"
    elif (source is not None and saving_difference == source
          and member_cue and not conflict):
        price_kind = "advertised_member_derived"
    elif source is not None and nonmember == source and not conflict:
        price_kind = "advertised_nonmember"
    elif source is not None:
        price_kind = "unclassified_advertised"

    return {
        "source_price_ore": source,
        "source_pre_price_ore": previous,
        "price_kind": price_kind,
        "explicit_nonmember_ore": nonmember,
        "explicit_member_ore": member,
        "explicit_previous_ore": before,
        "saving_ore": saving,
        "conditional_member_ore": confirmed_member,
        "review_only_possible_member_ore": (
            possible_member if confirmed_member is None else None),
        "member_evidence": member_evidence,
        "bundle_units": bundle_units,
        "bundle_member_only": bundle_units is not None and member_cue,
        "bundle_total_ore": bundle_total,
        "bundle_unit_equivalent_ore": bundle_unit_ore,
        "percent_condition_count": len(percentages),
        "pant_mentioned": has_pant,
        "review_reasons": sorted(set(flags)),
        "source_status": "source_only",
        "checked_shop_price": False,
        "rank_eligible": False,
    }
