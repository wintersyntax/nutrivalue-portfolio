"""TASK-033: offline publisher-PDF corroboration of *candidate* member prices.

A member price can be derived only when one API offer has:
 - explicit nonmember amount matching current API source price;
 - explicit saving amount, with positive exact øre arithmetic;
 - a single original-PDF title line on source catalog_page;
 - unique nearby printed nonmember, saving, and large member-price badge
   consistent with the API-derived amount.
No tier/checkout/store price is made rank eligible by this evidence.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Mapping, Sequence

from nutrivalue.providers.tjek_offer_conditions import interpret_offer
from nutrivalue.providers.tjek_pdf_geometry import Box, Page, _ore
from nutrivalue.providers.tjek_pdf_link import _normalized


SAVING_LINE = re.compile(
    r"(?<!\w)spar\s*(?:kr\.?\s*|:\s*)?(\d+(?:[.,]\d{1,2})?)(?!\w)", re.I)
NONMEMBER_LINE = re.compile(
    r"(?<!\w)ikke[\s-]*medlem(?:spris)?\s*:?\s*(\d+(?:[.,]\d{1,2})?)(?!\w)", re.I)
WHOLE = re.compile(r"\d{1,4}")
CENTS = re.compile(r"\d{2}")


def _local_lines(page: Page, title: Box, pattern: re.Pattern, ore: int) -> list[Box]:
    candidates = []
    for line in page.lines:
        if (abs(line.cx - title.cx) > 135
                or abs(line.cy - title.cy) > 175):
            continue
        matches = list(pattern.finditer(line.text))
        if len(matches) == 1 and _ore(matches[0].group(1)) == ore:
            candidates.append(line)
    return candidates


def _printed_member_badge(page: Page, title: Box, amount: int) -> bool:
    whole, cents = divmod(amount, 100)
    candidates = []
    for integer in page.words:
        if (not WHOLE.fullmatch(integer.text)
                or int(integer.text) != whole
                or integer.y1 - integer.y0 < 45
                or not 0 < title.y0 - integer.y1 <= 220
                or abs(integer.cx - title.cx) > 140):
            continue
        if cents == 0:
            candidates.append(integer)
            continue
        supporting_cents = [
            w for w in page.words
            if (CENTS.fullmatch(w.text) and int(w.text) == cents
                and integer.x1 - 15 <= w.x0 <= integer.x1 + 95
                and abs(w.cy - integer.cy) <= 75
                and w.cy < title.y0)
        ]
        if len(supporting_cents) == 1:
            candidates.append(integer)
    # A single large price badge corroborates the derived amount.
    return len(candidates) == 1


def correlate_member_badges(offers: Sequence[Mapping], pages: Sequence[Page]) -> list[dict]:
    """All output remains original-source-only and review-only; no price writes."""
    if not pages:
        raise ValueError("PDF geometry pages required")
    results = []
    ids = set()
    for offer in offers:
        if not isinstance(offer, Mapping):
            raise ValueError("invalid provider offer")
        oid, page_no, heading = (offer.get("id"), offer.get("catalog_page"),
                                 offer.get("heading"))
        if (not isinstance(oid, str) or not oid or oid in ids
                or type(page_no) is not int or not 1 <= page_no <= len(pages)
                or not isinstance(heading, str) or not heading.strip()):
            raise ValueError("invalid/duplicate offer id, page or title")
        ids.add(oid)
        row = {
            "source_offer_id": oid,
            "source_catalog_page": page_no,
            "state": "no_explicit_candidate",
            "member_amount_review_only_ore": None,
            "source_status": "source_only",
            "rank_eligible": False,
            "checked_shop_price": False,
        }
        results.append(row)
        candidate = interpret_offer(offer)
        nonmember = candidate["explicit_nonmember_ore"]
        saving = candidate["saving_ore"]
        possible_member = candidate["review_only_possible_member_ore"]
        if (nonmember is None or saving is None or possible_member is None
                or candidate["source_price_ore"] != nonmember
                or candidate["bundle_units"] is not None):
            continue
        row["state"] = "member_candidate_unmatched_pdf"
        title_text = _normalized(heading)
        if len(title_text) < 7:
            continue
        page = pages[page_no - 1]
        title_lines = [
            line for line in page.lines
            if re.search(r"(?<!\w)" + re.escape(title_text) + r"(?!\w)",
                         _normalized(line.text))
        ]
        # The PDF may print an umbrella name once and list variant names
        # beginning with the same text (e.g. LEGO Botanicals plus two sets).
        # Prefer ONE complete line equal to the API heading over prefix
        # matches; duplicate exact lines or multiple prefix-only lines are
        # still ambiguous, never "pick the closest price" by default.
        exact_title_lines = [
            line for line in title_lines if _normalized(line.text) == title_text
        ]
        if len(exact_title_lines) == 1:
            title = exact_title_lines[0]
        elif not exact_title_lines and len(title_lines) == 1:
            title = title_lines[0]
        else:
            continue
        printed_nonmember = _local_lines(page, title, NONMEMBER_LINE, nonmember)
        printed_savings = _local_lines(page, title, SAVING_LINE, saving)
        if (len(printed_nonmember) != 1 or len(printed_savings) != 1
                or not _printed_member_badge(page, title, possible_member)):
            continue
        row.update({
            "state": "pdf_member_arithmetic_correlated_review_only",
            "member_amount_review_only_ore": possible_member,
        })
    return results


def member_badge_summary(rows: Sequence[Mapping]) -> dict[str, int]:
    counts = Counter(row["state"] for row in rows)
    return {
        "member_candidates_explicit_api_nonmember_saving":
            counts["member_candidate_unmatched_pdf"] +
            counts["pdf_member_arithmetic_correlated_review_only"],
        "member_badge_correlated_review_only":
            counts["pdf_member_arithmetic_correlated_review_only"],
        "member_candidate_unmatched_pdf":
            counts["member_candidate_unmatched_pdf"],
        "membership_checkout_verified": 0,
        "member_price_rank_eligible": 0,
    }
