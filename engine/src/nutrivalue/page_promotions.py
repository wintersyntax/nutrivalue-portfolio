"""TASK-053/056: page-level promotion extraction contract, validation and scoring.

Catalogue pages without offer hotspots carry group promotions (N for M,
percent or krone off a product group, loyalty bonus, member-only terms)
that the Tjek offer API does not expose. This module defines the strict
output contract a vision model must follow, validates model output,
cross-checks extracted numbers against local OCR text and scores
predictions against a reviewed reference set. No HTTP here.
"""
from __future__ import annotations

import json
import re

KINDS = ("n_for_m", "percent_off", "amount_off", "bonus_percent")
PAGE_CLASSES = ("promotion", "non_offer", "offers")
MAX_PROMOTIONS = 60

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["page_class", "promotions"],
    "properties": {
        "page_class": {"type": "string", "enum": list(PAGE_CLASSES)},
        "promotions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "n", "m", "percent", "amount_nok", "member_only", "scope"],
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "n": {"type": ["integer", "null"]},
                    "m": {"type": ["integer", "null"]},
                    "percent": {"type": ["number", "null"]},
                    "amount_nok": {"type": ["number", "null"]},
                    "member_only": {"type": "boolean"},
                    "scope": {"type": "string"},
                },
            },
        },
    },
}

PROMPT_VERSION = "page_promotions@3"

PROMPT = """You read ONE page of a Norwegian grocery catalogue (kundeavis).
Return ONLY JSON matching the schema. Report only what is printed on this page.

page_class:
- "promotion": the page mainly advertises discounts that apply to a product
  group, a brand range or listed products WITHOUT a printed price per product
  (e.g. "3 for 2", "-20%", "Spar -10 kr", "Medlemsrabatt -15%", "40% Trumf-bonus").
- "offers": the page mainly shows individual products with printed prices.
- "non_offer": no concrete discount (ads, app/membership info, recipes,
  donations, competitions, new products without a discount).

promotions: report EVERY discount without a printed sale price, on ANY page
class - also a "-20%" banner or a "3 for 2" badge on a page that otherwise
shows priced products. One entry per distinct discount and scope:
- kind "n_for_m": take n items, pay for m items ("3 for 2": n=3, m=2;
  "2 for 1": n=2, m=1).
- kind "percent_off": percentage discount at checkout; percent = the number.
- kind "amount_off": fixed krone discount without a printed sale price
  ("Spar -10 kr", "-5kr"); amount_nok.
- kind "bonus_percent": loyalty bonus credited later (e.g. Trumf-bonus,
  frukt & grønt-bonus); percent.
- Unused numeric fields must be null.
- member_only: true only if the page says the discount requires membership
  (e.g. "Medlemspris", "Medlemsrabatt", "Coop Medlem", "for MER-kunder",
  "KIWI PLUSS", a loyalty app); otherwise false. The green keyhole symbol
  (Nøkkelhullet, a health label that can look like a person) is NEVER a
  membership mark.
- scope: short Norwegian text naming what the discount applies to, copied
  from the page (brands, product group, size limits). Do not invent products.
If several products share one discount badge, report ONE entry whose scope
lists them. If each product has its own badge (e.g. "Spar -5 kr" under every
product), report one entry PER PRODUCT - never merge them into one entry.

NOT promotions (never report these):
- A product with its own printed sale price, even when it also says "Spar kr
  13,50", "Medlemspris", "Partivare", "Førpris" or "Ikke medlem? 41,90".
- "N for <kroner amount>" such as "2 for 100", "3 for 90" or "2 for 50,00":
  that is a price.
- A coin, star or badge showing only a number without "%" or a minus sign
  (e.g. MENY "Kronemarked 20 / 25 / 30", "20,-"): that is a fixed price in
  kroner, not a discount.
- Examples shown inside app screenshots, phone mockups or coupon pictures, and
  any coupon whose printed expiry date lies outside the validity period below.
- Donations to charity ("1% går til ...", "2,50 kr doneres").
Never report the catalogue validity dates. Never guess unreadable numbers."""

CONTEXT_TEMPLATE = """

Catalogue context (from the source, not from the page):
- chain: {chain}
- catalogue: {label}
- valid: {valid_from} to {valid_to} (Norwegian time)"""


def _oslo(stamp: str) -> str:
    """'2026-10-11T22:00:00+0000' -> '2026-10-12' in Norwegian time (date only)."""
    from datetime import datetime, timedelta, timezone
    text = str(stamp or "").strip()
    if not text:
        return "unknown"
    text = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", text)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return "unknown"
    if moment.tzinfo is None:
        return moment.date().isoformat()
    try:
        from zoneinfo import ZoneInfo
        return moment.astimezone(ZoneInfo("Europe/Oslo")).date().isoformat()
    except Exception:   # no tz database: CET/CEST approximated as UTC+1
        return moment.astimezone(timezone(timedelta(hours=1))).date().isoformat()


def build_prompt(page: dict) -> str:
    """PROMPT plus catalogue context; unknown context fields are stated as unknown."""
    def clean(value) -> str:
        return re.sub(r"[\r\n]+", " ", str(value or "")).strip()[:120] or "unknown"
    return PROMPT + CONTEXT_TEMPLATE.format(
        chain=clean(page.get("chain")), label=clean(page.get("catalog_label")),
        valid_from=_oslo(page.get("valid_from")), valid_to=_oslo(page.get("valid_to")))


class PromotionOutputError(ValueError):
    """Model output does not satisfy the contract at page level."""


ITEM_FIELDS = frozenset(OUTPUT_SCHEMA["properties"]["promotions"]["items"]["properties"])


def _item(item) -> dict:
    """One promotion or a ValueError naming what is wrong with it."""
    if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
        raise ValueError("promotion has wrong fields")
    kind = item["kind"]
    if kind not in KINDS:
        raise ValueError("unknown promotion kind")
    if type(item["member_only"]) is not bool or not isinstance(item["scope"], str):
        raise ValueError("member_only/scope have wrong types")
    n, m, pct, amount = item["n"], item["m"], item["percent"], item["amount_nok"]
    for value in (n, m):
        if value is not None and type(value) is not int:
            raise ValueError("n/m must be integers")
    for value in (pct, amount):
        if value is not None and (type(value) not in (int, float) or value != value):
            raise ValueError("percent/amount must be numbers")
    if kind == "n_for_m":
        if n is None or m is None or not 1 <= m < n <= 20 or pct is not None or amount is not None:
            raise ValueError("n_for_m needs n > m >= 1 only")
    elif kind in ("percent_off", "bonus_percent"):
        if pct is None or not 0 < pct <= 100 or n is not None or m is not None or amount is not None:
            raise ValueError(f"{kind} needs percent only")
    elif amount is None or not 0 < amount <= 10_000 or n is not None or m is not None or pct is not None:
        raise ValueError("amount_off needs amount_nok only")
    return {"kind": kind, "n": n, "m": m,
            "percent": float(pct) if pct is not None else None,
            "amount_nok": float(amount) if amount is not None else None,
            "member_only": item["member_only"], "scope": item["scope"].strip()}


def validate_output(raw: str | dict) -> dict:
    """Parse one model answer.

    Page-level (structural) problems raise PromotionOutputError. A single
    malformed promotion does not invalidate the page: it is dropped and
    described in ``item_errors`` so valid neighbours are kept.
    """
    if isinstance(raw, str):
        text = raw.strip()
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
        if fence:
            text = fence.group(1)
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise PromotionOutputError("answer is not JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"page_class", "promotions"}:
        raise PromotionOutputError("answer must have exactly page_class and promotions")
    page_class = raw["page_class"]
    if page_class not in PAGE_CLASSES:
        raise PromotionOutputError("unknown page_class")
    items = raw["promotions"]
    if not isinstance(items, list) or len(items) > MAX_PROMOTIONS:
        raise PromotionOutputError("promotions must be a bounded list")
    out, errors, indexes = [], [], []
    for index, item in enumerate(items):
        try:
            out.append(_item(item))
            indexes.append(index)
        except ValueError as exc:
            errors.append({"index": index, "error": str(exc)})
    kept = []
    for index, promo in zip(indexes, out):
        if _restates_n_for_m(promo, out):
            errors.append({"index": index, "error": "percent restates an n_for_m on this page"})
        else:
            kept.append(promo)
    errors.sort(key=lambda e: e["index"])
    return {"page_class": page_class, "promotions": kept, "item_errors": errors}


def _restates_n_for_m(promo: dict, items: list[dict]) -> bool:
    """'3 for 2 - 33 % trekkes fra' is ONE deal: drop the percent restatement.

    A percent_off equal (within 1 point) to (n-m)/n of an n_for_m with the
    same member flag and a largely shared scope is the same discount. "Shared"
    is measured as containment (shared words / words of the shorter scope): the
    restatement usually adds "trekkes fra i kassen" while the badge adds "plukk
    og miks", which kept W44 REMA "all kjeks" below a 0.5 Jaccard overlap.
    """
    if promo["kind"] != "percent_off":
        return False
    for other in items:
        if (other["kind"] == "n_for_m" and other["member_only"] == promo["member_only"]
                and abs(100 * (other["n"] - other["m"]) / other["n"] - promo["percent"]) <= 1
                and scope_containment(other["scope"], promo["scope"]) >= 0.5):
            return True
    return False


def _value(promo: dict) -> tuple:
    if promo["kind"] == "n_for_m":
        return (promo["n"], promo["m"])
    if promo["kind"] == "amount_off":
        return (round(float(promo["amount_nok"]), 2),)
    return (round(float(promo["percent"]), 2),)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[^\W_]+", text.casefold()) if len(t) > 2}


def scope_overlap(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def scope_containment(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def score_page(reference: dict, predicted: dict | None) -> dict:
    """Strict match on kind + numbers + member_only; scope reported separately.

    Reference items marked optional are neither rewarded nor penalised.
    A missing/invalid prediction counts every required item as missed.
    """
    required = [p for p in reference["promotions"] if not p.get("optional")]
    optional = [p for p in reference["promotions"] if p.get("optional")]
    result = {"page_class_correct": False, "tp": 0, "fp": 0, "fn": 0,
              "member_errors": 0, "scope_overlaps": []}
    if predicted is None:
        result["fn"] = len(required)
        return result
    accepted = {reference["page_class"], *reference.get("acceptable_page_classes", [])}
    result["page_class_correct"] = predicted["page_class"] in accepted
    pool = list(required)
    spare = list(optional)
    for promo in predicted["promotions"]:
        key = (promo["kind"], _value(promo))
        match = next((r for r in pool if (r["kind"], _value(r)) == key
                      and r["member_only"] == promo["member_only"]), None)
        if match is None:
            near = next((r for r in pool if (r["kind"], _value(r)) == key), None)
            if near is not None:
                result["member_errors"] += 1
        if match is not None:
            # Several identical-value entries (e.g. -10 kr items): best scope wins.
            same = [r for r in pool if (r["kind"], _value(r)) == key
                    and r["member_only"] == promo["member_only"]]
            match = max(same, key=lambda r: scope_overlap(r["scope"], promo["scope"]))
            pool.remove(match)
            result["tp"] += 1
            result["scope_overlaps"].append(round(scope_overlap(match["scope"], promo["scope"]), 3))
            continue
        extra = next((r for r in spare if (r["kind"], _value(r)) == key), None)
        if extra is not None:
            spare.remove(extra)
            continue
        result["fp"] += 1
    result["fn"] = len(pool)
    return result


def numbers_supported(promotions: list[dict], ocr_text: str) -> list[bool]:
    """Each extracted number must appear as a standalone number in OCR text."""
    found = set(re.findall(r"(?<!\d)(\d{1,4})(?:[.,]\d{1,2})?(?!\d)", ocr_text))
    flags = []
    for promo in promotions:
        if promo["kind"] == "n_for_m":
            needed = {str(promo["n"]), str(promo["m"])}
        elif promo["kind"] == "amount_off":
            needed = {str(int(promo["amount_nok"]))}
        else:
            needed = {str(int(promo["percent"]))}
        flags.append(needed <= found)
    return flags


FOR_TOKEN = r"(?:for|f[o0]r|[ft][o0]r?|fo)"   # common OCR readings of "for"


def ocr_lines(tokens: list[tuple[str, list]]) -> list[str]:
    """OCR tokens plus left-to-right chains of tokens on one visual line.

    Large badges are often split ("3" | "FOR" | "2", "-30°" | "%"); joining
    tokens that overlap vertically and nearly touch lets a pattern see them.
    Each token is (text, box) with box = 4 [x, y] corners.
    """
    items = []
    for text, box in tokens:
        xs = [float(c[0]) for c in box]
        ys = [float(c[1]) for c in box]
        items.append((min(xs), max(xs), min(ys), max(ys), text))
    items.sort(key=lambda i: i[0])
    out = [i[4] for i in items]
    for start in items:
        chain, cur = [start], start
        for nxt in items:
            if nxt is cur or nxt[0] < cur[0]:
                continue
            height = min(cur[3] - cur[2], nxt[3] - nxt[2])
            overlap = min(cur[3], nxt[3]) - max(cur[2], nxt[2])
            gap = nxt[0] - cur[1]
            if (height > 0 and overlap > 0.4 * height
                    and gap < 0.6 * max(cur[3] - cur[2], nxt[3] - nxt[2])):
                chain.append(nxt)
                cur = nxt
        if len(chain) > 1:
            out.append(" ".join(c[4] for c in chain))
    return [re.sub(r"\s+", " ", line).casefold() for line in out]


def _pattern(promo: dict) -> str:
    if promo["kind"] == "n_for_m":
        n, m = promo["n"], promo["m"]
        share = 100 * (n - m) / n
        percents = "|".join(sorted({str(int(share)), str(round(share))}))
        return (rf"(?<!\d){n}\s*{FOR_TOKEN}\s*{m}(?!\d)"
                # badge with a vertical "FOR" read as one token: "302", "382", "281"
                rf"|^{n}[o08]{m}$"
                # the printed equivalent: "33% rabatt ved kjøp av 3"
                rf"|(?<!\d)(?:{percents})\s*[°o]?\s*%\s*rabatt\s+ved\s+kj[oø0]p\s+av\s+{n}(?!\d)")
    if promo["kind"] == "amount_off":
        return rf"(?<!\d)-?\s*{int(promo['amount_nok'])}\s*(?:kr|,-)"
    return rf"(?<!\d){int(promo['percent'])}\s*[°o]?\s*%"


def ocr_status(promotions: list[dict], lines: list[str]) -> list[str]:
    """Per promotion: 'confirmed' (badge pattern seen), 'number_only' or 'absent'.

    Measured on the W43 reference with RapidOCR tokens: 70/71 true values
    confirmed, 1/71 deliberately wrong values confirmed. Evidence, not truth.
    """
    text = "\n".join(lines)
    numbers = numbers_supported(promotions, text)
    out = []
    for promo, has_number in zip(promotions, numbers):
        if any(re.search(_pattern(promo), line) for line in lines):
            out.append("confirmed")
        else:
            out.append("number_only" if has_number else "absent")
    return out
