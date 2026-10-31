"""Offline candidate checks adapted from private NutriValue TASK-069/TASK-070.

All inputs are caller-provided dictionaries. No HTTP requests, credentials,
databases, provider responses or personal observations are bundled.
A 'likely' result means REVIEW PRIORITY, not verified identity or store price.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation


def _fold(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").lower().split())


def _tokens(value: str | None) -> set[str]:
    return {token for token in re.findall(r"[a-zæøå]+", _fold(value))
            if len(token) >= 4}


def grams(quantity, unit) -> Decimal | None:
    try:
        amount = Decimal(str(quantity))
    except (TypeError, ValueError, InvalidOperation):
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    factor = {"g": 1, "gr": 1, "gram": 1, "hg": 100, "kg": 1000}.get(_fold(unit))
    return amount * factor if factor is not None else None


def name_grams(name: str) -> Decimal | None:
    matches = re.findall(
        r"(?:(\d+)\s*x\s*)?(?:ca\.?\s*)?(\d+(?:[.,]\d+)?)\s*(kg|gr|g)\b",
        name or "", flags=re.I)
    if not matches:
        return None
    count, amount, unit = matches[-1]
    mass = grams(amount.replace(",", "."), unit)
    return mass * int(count) if mass is not None and count else mass


def offer_grams(offer: dict) -> Decimal | None:
    mass = grams(offer.get("quantity"), offer.get("unit"))
    count = offer.get("pack_count")
    return mass * count if mass is not None and isinstance(count, int) and count > 1 else mass


def offer_brand(description: str | None) -> str | None:
    stop = {"pr", "kg", "stk", "pk", "fersk", "fryst", "naturell",
            "uten", "med", "alltid", "billigere", "førpris", "spar", "ca"}
    for candidate in re.split(r"[,\n.]", description or ""):
        candidate = candidate.strip()
        words = candidate.split()
        if (candidate and len(candidate) <= 30 and not re.search(r"\d", candidate)
                and words and words[0][:1].isupper()
                and _fold(words[0]) not in stop):
            return candidate
    return None


def search_term(offer: dict) -> str:
    title = _fold(offer["heading"])
    brand = offer_brand(offer.get("description"))
    return f"{title} {_fold(brand)}" if brand and _fold(brand) not in title else title


def _nutrition(rows, label_field: str) -> dict:
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        label, unit = _fold(row.get(label_field)), _fold(row.get("unit"))
        try:
            value = Decimal(str(row.get("amount")))
        except (TypeError, ValueError, InvalidOperation):
            continue
        if not value.is_finite():
            continue
        if label in ("energi_kcal", "kalorier") and unit == "kcal":
            out["kcal_field"] = value
        elif label in ("energi_kj", "energi") and unit == "kj":
            out["kj"] = value
        elif label == "protein" and unit == "g":
            out["protein_g"] = value
    return out


def ngdata_candidates(payload: dict) -> list[dict]:
    """Normalize pre-supplied NGData-shaped JSON, without fetching it."""
    result = []
    for hit in (payload.get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        mass = (grams(src.get("measurementValue"), src.get("measurementType"))
                or name_grams(f"{src.get('title') or ''} {src.get('subtitle') or ''}"))
        result.append({
            "source": "ngdata_meny", "ean": src.get("ean"),
            "brand": src.get("brand"),
            "name": " ".join(filter(None, (src.get("title"), src.get("subtitle")))),
            "grams": mass, "price_nok": src.get("pricePerUnit"),
            "nutrition": _nutrition(src.get("nutritionalContent"), "name"),
        })
    return result


def kassalapp_candidates(payload: dict) -> list[dict]:
    """Normalize pre-supplied Kassalapp-shaped JSON, without fetching it."""
    result = []
    for src in payload.get("data") or []:
        if not isinstance(src, dict):
            continue
        result.append({
            "source": "kassalapp", "ean": src.get("ean"),
            "brand": src.get("brand"), "name": str(src.get("name") or ""),
            "grams": (grams(src.get("weight"), src.get("weight_unit"))
                      or name_grams(str(src.get("name") or ""))),
            "price_nok": src.get("current_price"),
            "nutrition": _nutrition(src.get("nutrition"), "code"),
        })
    return result


def nutrition_flags(values: dict) -> list[str]:
    protein, kcal, kj = (values.get(key) for key in
                         ("protein_g", "kcal_field", "kj"))
    flags = []
    if protein is None:
        flags.append("no protein value")
    elif not 0 <= protein <= 100:
        flags.append("protein outside 0-100 g")
    if kcal is None:
        flags.append("no kcal value")
    elif kcal < 0:
        flags.append("negative kcal")
    if kcal is not None and kj is not None and kj > 0:
        if abs(kj / Decimal("4.184") - kcal) > max(Decimal("5"), kcal * Decimal("0.08")):
            flags.append("kcal field holds the kJ value" if abs(kj - kcal) <= 1
                         else "kJ and kcal disagree")
    if kcal is not None and protein is not None and kcal + 5 < protein * 4:
        flags.append("energy below protein energy")
    flags.append("basis not stated (per-100-g assumption unverified)")
    return flags


def reference_flags(values: dict, reference: dict | None) -> list[str]:
    """Large gaps are review flags, not proof the candidate is wrong."""
    if reference is None:
        return []
    result = []
    for candidate_field, reference_field, low, high in (
        ("protein_g", "protein_g", Decimal("0.7"), Decimal("1.4")),
        ("kcal_field", "kcal", Decimal("0.6"), Decimal("1.6")),
    ):
        observed, expected = values.get(candidate_field), reference.get(reference_field)
        if observed is None or not expected:
            continue
        if not low <= observed / expected <= high:
            result.append(f"{candidate_field}: {observed} vs reference {expected}")
    return result


def score(offer: dict, candidate: dict) -> tuple[int, list[str]]:
    points, why = 0, []
    mass, candidate_mass = offer_grams(offer), candidate.get("grams")
    if mass is not None and candidate_mass is not None:
        delta = abs(candidate_mass - mass) / mass
        if delta <= Decimal("0.02"):
            points += 3
            why.append("same pack")
        elif delta > Decimal("0.10"):
            points -= 2
            why.append("pack mismatch")
    brand = offer_brand(offer.get("description"))
    name = f"{candidate.get('brand') or ''} {candidate.get('name') or ''}"
    if brand and _fold(brand) in _fold(name):
        points += 2
        why.append("brand")
    overlap = _tokens(offer["heading"]) & _tokens(name)
    if overlap:
        points += min(2, len(overlap))
        why.append("name")
    try:
        if (offer.get("price_ore") is not None and
                Decimal(str(candidate.get("price_nok"))) * 100 ==
                Decimal(str(offer["price_ore"]))):
            points += 1
            why.append("same price")
    except (InvalidOperation, TypeError, ValueError):
        pass
    return points, why


def assess(offer: dict, candidates: list[dict],
           reference: dict | None = None) -> dict:
    ranked = []
    for candidate in candidates:
        points, why = score(offer, candidate)
        ranked.append({**candidate, "score": points, "why": why,
                       "flags": nutrition_flags(candidate.get("nutrition") or {}),
                       "reference_flags": reference_flags(
                           candidate.get("nutrition") or {}, reference)})
    ranked.sort(key=lambda candidate: -candidate["score"])
    best_by_source = {}
    for candidate in ranked:
        best_by_source.setdefault(candidate["source"], candidate)
    best = list(best_by_source.values())
    status = "no candidate"
    if best:
        top = max(best, key=lambda candidate: candidate["score"])
        status = ("likely" if top["score"] >= 5 and "same pack" in top["why"]
                  else "possible")
        if (status == "likely" and len(best) == 2 and
                best[0].get("ean") and best[0]["ean"] == best[1].get("ean")):
            status = "likely, same EAN from both sources"
        if status.startswith("likely") and top["reference_flags"]:
            status = "check nutrition"
    checked = [candidate for candidate in ranked
               if reference and reference.get("protein_g")
               and candidate["nutrition"].get("protein_g") is not None]
    classification_check = None
    if checked and all(any(flag.startswith("protein_g:") for flag in
                           candidate["reference_flags"]) for candidate in checked):
        classification_check = "the offer may be misclassified"
    return {"offer": offer, "status": status, "candidates": ranked,
            "reference": reference, "classification_check": classification_check}
