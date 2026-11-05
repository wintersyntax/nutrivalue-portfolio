"""TASK-033: conservative OFFLINE supplement evidence for Tjek advertisements.

eTilbudsavis is itself a Tjek app: its structured fields are another
Tjek-provider view, NOT independent prices. External product/catalogue
sources may suggest EAN/package facts but cannot claim a SKU match merely
from a similar name. This module does not call HTTP or write any store,
price, inventory or nutrition records. Results are source_only/review_only.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Mapping
from urllib.parse import urlsplit

from nutrivalue.providers.tjek_pdf_link import _normalized
from nutrivalue.providers.tjek_unit_price import _printed, _quantity


def _ore(raw: object) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        money = Decimal(str(raw).strip().replace(",", "."))
        cents = money * 100
        if (not money.is_finite() or not 0 < money <= 100000
                or cents != cents.to_integral_value()):
            return None
        return int(cents)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _positive(raw: object) -> Decimal | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        n = Decimal(str(raw).replace(",", "."))
        return n if n.is_finite() and 0 < n <= 100000 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or len(raw) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def match_etilbudsavis_view(offer: Mapping, view: Mapping) -> dict:
    """Read one user-supplied eTilbudsavis offer record; never fetch it.

    Gate on exact Tjek dealer+publication IDs, full normalized heading,
    original advertised price and overlapping validity intervals. Prefer
    an identical immutable offer ID where both source views provide it.
    A 3-FOR-2 field is a buy/pay count, NOT an advertised monetary total.
    No candidate proves local-store eligibility or price ranking.
    """
    if not isinstance(offer, Mapping) or not isinstance(view, Mapping):
        raise ValueError("Tjek offer and public view must be mappings")
    output = {
        "state": "uncorrelated_other_provider_view",
        "source_kind": "etilbudsavis_tjek_same_upstream",
        "membership_price_review_only_ore": None,
        "buy_units_review_only": None,
        "pay_units_review_only": None,
        "bundle_total_review_only_ore": None,
        "percentage_scope_review_required": False,
        "rank_eligible": False, "checkout_verified": False,
        "source_status": "source_only",
    }
    dealer, publication = offer.get("dealer_id"), offer.get("catalog_id")
    if not (isinstance(dealer, str) and dealer and
            isinstance(publication, str) and publication and
            view.get("businessPublicId") == dealer and
            view.get("publicationPublicId") == publication):
        return output
    view_id = view.get("publicId")
    offer_id = offer.get("id")
    if isinstance(view_id, str) and view_id:
        if not isinstance(offer_id, str) or offer_id != view_id:
            output["state"] = "same_catalog_offer_id_conflict_review_only"
            return output
    heading, name = offer.get("heading"), view.get("name")
    if not (isinstance(heading, str) and isinstance(name, str)
            and _normalized(heading) == _normalized(name)
            and len(_normalized(heading)) >= 7):
        output["state"] = "same_catalog_name_review_only"
        return output
    pricing = offer.get("pricing")
    if (not isinstance(pricing, Mapping)
            or pricing.get("currency") != "NOK"
            or view.get("currencyCode") != "NOK"
            or _ore(pricing.get("price")) is None
            or _ore(pricing.get("price")) != _ore(view.get("price"))):
        output["state"] = "same_catalog_price_conflict_review_only"
        return output
    source_start = _timestamp(offer.get("run_from"))
    source_end = _timestamp(offer.get("run_till"))
    view_start = _timestamp(view.get("validFrom"))
    view_end = _timestamp(view.get("validUntil"))
    if not all((source_start, source_end, view_start, view_end)):
        output["state"] = "missing_or_invalid_validity_review_only"
        return output
    if (source_start > source_end or view_start > view_end
            or source_start > view_end or view_start > source_end):
        output["state"] = "validity_conflict_review_only"
        return output
    output["state"] = "correlated_same_tjek_provider_review_only"
    source_ore = _ore(pricing.get("price"))
    member_ore = _ore(view.get("membershipPrice"))
    if member_ore is not None:
        if member_ore < source_ore:
            output["membership_price_review_only_ore"] = member_ore
        else:
            output["state"] = "member_tier_invalid_or_already_discounted_review_only"
    # eTilbudsavis' pieceCountGet / pieceCountGetFor express a
    # "get N, pay M" QUANTITY promotion (e.g. 3 FOR 2 -> 3, 2).
    # They are NOT NOK prices. Keep the two integer counts as conditional
    # review evidence, without deriving a discounted basket total.
    get_count, pay_count = (view.get("pieceCountGet"),
                            view.get("pieceCountGetFor"))
    if (type(get_count) is int and type(pay_count) is int
            and 2 <= get_count <= 12 and 1 <= pay_count < get_count):
        output["buy_units_review_only"] = get_count
        output["pay_units_review_only"] = pay_count
    for key in ("relativeSavings", "membershipRelativeSavings"):
        if view.get(key) is not None:
            output["percentage_scope_review_required"] = True
    return output


GTIN13 = re.compile(r"^[0-9]{13}$")


def valid_gtin13(value: object) -> bool:
    if not isinstance(value, str) or GTIN13.fullmatch(value) is None:
        return False
    subtotal = sum(
        int(d) * (3 if i % 2 else 1) for i, d in enumerate(value[:12])
    )
    return (10 - subtotal % 10) % 10 == int(value[-1])


def inspect_product_fact(fact: Mapping, *,
                         approved_offer_gtin: str | None = None) -> dict:
    """Read a Kassalapp/manufacturer/Oda-style saved product fact.

    A Kassalapp search can supply EAN and package weight, but the name
    alone MUST NOT link it to a particular Tjek advertised variant.
    GTIN-13 plus an explicit, independently reviewed offer->GTIN
    attestation is necessary before a fact can participate in review.
    """
    if not isinstance(fact, Mapping):
        raise ValueError("product fact must be a mapping")
    source = fact.get("source")
    if source not in {"kassalapp", "manufacturer", "oda", "sesum"}:
        raise ValueError("unsupported named product-information source")
    ean = fact.get("ean")
    if not valid_gtin13(ean):
        return {
            "state": "no_valid_gtin_product_candidate",
            "ean": None, "declared_pack_g": None,
            "drained_mass_g": None, "rank_eligible": False,
        }
    weight = _positive(fact.get("weight"))
    unit = fact.get("weight_unit")
    factor = {"g": Decimal("1"), "kg": Decimal("1000")}.get(unit)
    pack = weight * factor if weight and factor else None
    # A 'weight' field in a search result is NOT by itself an
    # independently verified nutritional edible/drained mass.
    source_url = fact.get("source_url")
    checked_on = fact.get("checked_on")
    if isinstance(source_url, str):
        parsed_url = urlsplit(source_url)
        safe_url = (parsed_url.scheme == "https"
                    and bool(parsed_url.hostname)
                    and not parsed_url.username and not parsed_url.password
                    and len(source_url) <= 2048)
    else:
        safe_url = False
    try:
        valid_checked = (isinstance(checked_on, str)
                         and date.fromisoformat(checked_on).isoformat() == checked_on)
    except ValueError:
        valid_checked = False
    approved = (approved_offer_gtin is not None
                and valid_gtin13(approved_offer_gtin)
                and approved_offer_gtin == ean
                and fact.get("reviewer_confirmed") is True
                and safe_url and valid_checked)
    drained = None
    if approved and source == "manufacturer":
        drained = _positive(fact.get("drained_weight_g"))
        if drained is not None and (pack is None or drained > pack):
            drained = None
    return {
        "state": ("attested_exact_gtin_product_fact_review_only"
                  if approved else "unlinked_gtin_product_candidate"),
        "ean": ean,
        "declared_pack_g": str(pack) if approved and pack else None,
        "drained_mass_g": str(drained) if drained else None,
        "mass_basis": "declared_pack_not_edible_mass_verified",
        "source": source,
        "rank_eligible": False,
    }


CARTON_TOTAL = re.compile(
    r"\b(?:kartong|kasse)\b[^\n.]{0,45}?"
    r"(?P<kg>\d{1,2}(?:[.,]\d{1,2})?)\s*kg"
    r"[^\n.]{0,35}?"
    r"(?:for|til|kr\.?)\s*(?P<total>\d{2,5}(?:[.,]\d{1,2})?)\b", re.I)


def parse_carton_condition(offer: Mapping) -> dict:
    """Only explicit text 'kartong 5 kg ... for 699' is a candidate.

    Never change the ordinary 1kg source price into the carton rate;
    keep both independent basket alternatives with minimum quantity.
    """
    if not isinstance(offer, Mapping):
        raise ValueError("offer required")
    text = offer.get("description") or ""
    if not isinstance(text, str):
        raise ValueError("description required")
    choices = list(CARTON_TOTAL.finditer(text))
    out = {
        "state": "no_explicit_carton_total",
        "minimum_carton_kg": None, "carton_total_review_only_ore": None,
        "carton_ore_per_kg_review_only": None,
        "source_status": "source_only", "rank_eligible": False,
    }
    if len(choices) != 1:
        if len(choices) > 1:
            out["state"] = "ambiguous_carton_mentions"
        return out
    m = choices[0]
    mass, total = _positive(m.group("kg")), _ore(m.group("total"))
    if mass is None or total is None or mass <= 1:
        return out
    # Do not substitute a former/reference carton amount for a
    # live promotion without explicit source scoping.
    near = text[max(0, m.start() - 20):m.start()].casefold()
    if re.search(r"\b(?:førpris|ord\.?\s*pris)\s*$", near):
        out["state"] = "carton_reference_not_promotion"
        return out
    rate = (Decimal(total) / mass).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)
    out.update({
        "state": "explicit_carton_basket_review_only",
        "minimum_carton_kg": str(mass),
        "carton_total_review_only_ore": total,
        "carton_ore_per_kg_review_only": int(rate),
    })
    return out


def diagnose_alternative_mass_basis(offer: Mapping) -> dict:
    """Arithmetic-only possible denominator for a printed kg-rate mismatch.

    This NEVER says an inferred weight is drained mass, a new SKU, or a
    truthful nutrition denominator. A GTIN-linked label must confirm it.
    """
    out = {
        "state": "no_arithmetic_alternative_mass",
        "inferred_grams_review_only": None,
        "rank_eligible": False,
    }
    if not isinstance(offer, Mapping):
        return out
    pricing = offer.get("pricing")
    if not isinstance(pricing, Mapping):
        return out
    price = _ore(pricing.get("price"))
    basis, pack_quantity, status = _quantity(offer.get("quantity"))
    if (price is None or basis != "kg" or status != "ok"
            or pack_quantity is None):
        return out
    printed = [(p, lower) for b, p, lower
               in _printed(offer.get("description") or "") if b == "kg"]
    if len(printed) != 1 or printed[0][1] or printed[0][0] == 0:
        return out
    rate = printed[0][0]
    candidate_g = (Decimal(price) * Decimal("1000")) / Decimal(rate)
    pack_g = pack_quantity * 1000
    nearest = candidate_g.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    # Whole-gram candidate must closely reproduce the stated rate.
    if (nearest <= 0 or nearest >= pack_g
            or abs(candidate_g - nearest) > Decimal("0.05")):
        return out
    out["state"] = "arithmetic_smaller_mass_hypothesis_review_only"
    out["inferred_grams_review_only"] = int(nearest)
    return out
