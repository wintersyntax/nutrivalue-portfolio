"""TASK-055: cross-check of disputed promotions by the other model.

When only one model reports an item, the OTHER model is shown that claim
(never its own earlier answer) together with one deliberately wrong decoy
and asked, from the page image, whether exactly this discount is printed,
whether it is for members only and what the badge says. An item is accepted
only if the verifier confirms it with the same member flag, rejects the
decoy, and local OCR confirms the badge. It is refuted (rejected without a
person, still visible for spot-checks) only if the verifier says it is not
printed, rejects the decoy, and OCR finds no such badge either. Anything
else stays in review.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from nutrivalue.page_promotions import (
    CONTEXT_TEMPLATE, PromotionOutputError, _oslo, ocr_status,
)
from nutrivalue.promotion_benchmark import _key, _slug, _write

VERSION = "crosscheck@1"
MAX_CLAIMS = 40

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["checks"],
    "properties": {"checks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "printed", "member_only", "evidence"],
        "properties": {"id": {"type": "integer"}, "printed": {"type": "boolean"},
                       "member_only": {"type": "boolean"}, "evidence": {"type": "string"}}}}},
}

PROMPT = """You verify claims about ONE page of a Norwegian grocery catalogue
(kundeavis). Another reader listed the discounts below. Some claims are wrong
on purpose. Decide EACH claim independently and only from this page image.

For each claim return:
- id: the claim number.
- printed: true only if exactly this discount (same kind and same number) is
  printed on this page for these products; false otherwise.
- member_only: true only if the page says this discount requires membership
  ("Medlem", "Coop Medlem", "Medlemsrabatt", "MER-kunde", "KIWI PLUSS",
  "Trumf", a loyalty app). The green keyhole symbol (Nøkkelhullet) is a health
  label, NOT membership.
- evidence: the exact text of the badge as printed (empty if not printed).

Not discounts: a product with its own printed sale price ("Spar kr 13,50"
next to a price, "Medlemspris 59,90"); "N for <kroner>" such as "2 for 50,00";
a coin or badge with only a number (MENY "Kronemarked 20 / 25 / 30"); app
screenshots and charity donations.

Claims:
{claims}"""


def describe(promo: dict) -> str:
    if promo["kind"] == "n_for_m":
        text = f"{promo['n']} for {promo['m']}"
    elif promo["kind"] == "percent_off":
        text = f"-{promo['percent']:g} %"
    elif promo["kind"] == "amount_off":
        text = f"-{promo['amount_nok']:g} kr"
    else:
        text = f"{promo['percent']:g} % bonus"
    return text


def decoy(claim: dict) -> dict:
    """The same products with a wrong number (never equal to any real value)."""
    fake = dict(claim)
    if claim["kind"] == "n_for_m":
        fake["n"], fake["m"] = claim["n"] + 2, claim["m"] + 2
    elif claim["kind"] == "amount_off":
        fake["amount_nok"] = float(claim["amount_nok"]) + 7
    else:
        value = float(claim["percent"])
        fake["percent"] = value + 15 if value <= 80 else value - 35
    return fake


def _page_key(page: dict) -> str:
    return f"{page['catalog_id']}|{page['page']}"


def claims_for(page: dict, claims: list[dict]) -> list[dict]:
    """Numbered claims with one decoy at a deterministic, page-dependent slot."""
    claims = claims[:MAX_CLAIMS]
    slot = int(hashlib.sha256(_page_key(page).encode()).hexdigest(), 16) % (len(claims) + 1)
    real_values = {_key(c)[:2] for c in claims}
    fake = decoy(claims[slot % len(claims)])
    while _key(fake)[:2] in real_values:          # keep the decoy distinct
        fake = decoy(fake)
    ordered = claims[:slot] + [fake] + claims[slot:]
    out = []
    for number, claim in enumerate(ordered, 1):
        out.append({"id": number, "decoy": claim is fake, "kind": claim["kind"],
                    "n": claim["n"], "m": claim["m"], "percent": claim["percent"],
                    "amount_nok": claim["amount_nok"], "member_only": claim["member_only"],
                    "scope": claim["scope"]})
    return out


def build_prompt(page: dict, numbered: list[dict]) -> str:
    lines = [f"{c['id']}. {describe(c)} - {c['scope']}" for c in numbered]
    clean = lambda v: re.sub(r"[\r\n]+", " ", str(v or "")).strip()[:120] or "unknown"  # noqa: E731
    return PROMPT.format(claims="\n".join(lines)) + CONTEXT_TEMPLATE.format(
        chain=clean(page.get("chain")), label=clean(page.get("catalog_label")),
        valid_from=_oslo(page.get("valid_from")), valid_to=_oslo(page.get("valid_to")))


def path_for(out_dir: Path, verifier: str, page: dict) -> Path:
    return (Path(out_dir) / "crosscheck" / _slug(verifier)
            / f"{page['catalog_id']}_p{page['page']:03d}.json")


def parse(content: str, numbered: list[dict]) -> dict[int, dict]:
    """Checks by claim id; raises PromotionOutputError when unusable."""
    text = (content or "").strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PromotionOutputError("cross-check answer is not JSON") from exc
    checks = data.get("checks") if isinstance(data, dict) else None
    if not isinstance(checks, list):
        raise PromotionOutputError("cross-check answer lacks checks")
    out = {}
    for check in checks:
        if (not isinstance(check, dict) or type(check.get("id")) is not int
                or type(check.get("printed")) is not bool
                or type(check.get("member_only")) is not bool
                or not isinstance(check.get("evidence"), str)):
            raise PromotionOutputError("malformed cross-check item")
        out[check["id"]] = check
    if set(out) != {c["id"] for c in numbered}:
        raise PromotionOutputError("cross-check must answer every claim exactly once")
    return out


def decide(saved: dict | None, claim: dict, ocr_lines: list[str] | None) -> dict:
    """Outcome for one disputed claim: {'accepted': bool, 'reason': str, ...}."""
    if saved is None:
        return {"accepted": False, "reason": "not cross-checked"}
    numbered = saved["claims"]
    try:
        checks = parse(saved["answer"].get("content") or "", numbered)
    except PromotionOutputError as exc:
        return {"accepted": False, "reason": f"unusable cross-check: {exc}"}
    fake = next(c for c in numbered if c["decoy"])
    if checks[fake["id"]]["printed"]:
        return {"accepted": False, "reason": "verifier also confirmed the decoy"}
    target = next((c for c in numbered if not c["decoy"] and _key(c) == _key(claim)
                   and c["scope"] == claim["scope"]), None)
    if target is None:
        return {"accepted": False, "reason": "claim not in the cross-check"}
    check = checks[target["id"]]
    result = {"verifier_printed": check["printed"],
              "verifier_member_only": check["member_only"], "evidence": check["evidence"]}
    if not check["printed"]:
        if ocr_lines is not None and ocr_status([claim], ocr_lines) != ["confirmed"]:
            return {**result, "accepted": False, "refuted": True,
                    "reason": "verifier says not printed and OCR finds no such badge"}
        return {**result, "accepted": False, "reason": "verifier says not printed"}
    if check["member_only"] != claim["member_only"]:
        return {**result, "accepted": False, "reason": "verifier disagrees on members-only"}
    if ocr_lines is None or ocr_status([claim], ocr_lines) != ["confirmed"]:
        return {**result, "accepted": False, "reason": "badge not confirmed by OCR"}
    return {**result, "accepted": True, "reason": "confirmed by the other model and OCR"}


def save(out_dir: Path, verifier: str, page: dict, numbered: list[dict], prompt: str,
         call: dict) -> None:
    _write(path_for(out_dir, verifier, page), {
        "version": VERSION, "verifier": verifier, "catalog_id": page["catalog_id"],
        "page": page["page"], "image_sha256": page["sha256"], "claims": numbered,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "provider_policy": call["policy"], "price_ceiling_usd": call["ceiling"],
        "over_price_ceiling": call["over"], "answer": call["answer"]})


def load(out_dir: Path, verifier: str, page: dict, numbered: list[dict]) -> dict | None:
    """The saved cross-check if it is for this image and exactly these claims."""
    path = path_for(out_dir, verifier, page)
    if not path.exists():
        return None
    saved = json.loads(path.read_text())
    if saved.get("image_sha256") != page["sha256"] or saved.get("claims") != numbered:
        raise PromotionOutputError(f"stale cross-check for {page['catalog_id']} p{page['page']}")
    return saved
