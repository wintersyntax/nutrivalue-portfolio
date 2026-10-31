"""TASK-033: conservative OFFLINE Tjek-offer -> publisher-PDF page-title join.

This first stage never guesses the badge price, bundle quantity, member tier,
SKU, shelf applicability, or new provider IDs. A title on the right page is
NOT evidence that an adjacent printed price belongs to it.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Mapping, Sequence


def _normalized(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("expected PDF or provider text")
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.sub(r"[^\wæøå]+", " ", text).split())


def physical_text_pages(text: str) -> list[str]:
    """Preserve physical PDF page indices, including text-empty PDF pages.

    Poppler's pdftotext separates pages with form feeds and emits a final
    terminator. Removing all empty page segments would shift catalog_page
    indices if the blank page is in the middle of a publication.
    """
    if not isinstance(text, str) or not text:
        raise ValueError("nonempty PDF text is required")
    pages = text.split("\f")
    if text.endswith("\f"):
        pages.pop()  # terminator, not an extra physical page
    if not pages or not any(page.strip() for page in pages):
        raise ValueError("PDF has no readable page text")
    return pages


def match_page_titles(offers: Sequence[Mapping], pages: Sequence[str]) -> list[dict]:
    """Link original API title to source-advertised PDF page; review ambiguity.

    Confidence is only for the title/page correlation. Token presence is
    *review-only*, even on a known page: tokens may refer to multiple cards.
    No matching to prices or discount badges occurs in this stage.
    """
    if not pages or any(not isinstance(page, str) for page in pages):
        raise ValueError("readable PDF page text is required")
    normalized_pages = [_normalized(page) for page in pages]
    results = []
    seen = set()
    for offer in offers:
        if not isinstance(offer, Mapping):
            raise ValueError("offer must be an object")
        oid = offer.get("id")
        page_no = offer.get("catalog_page")
        heading = offer.get("heading")
        cat = offer.get("catalog_id")
        if (not isinstance(oid, str) or not oid or oid in seen
                or type(page_no) is not int or page_no < 1 or page_no > len(pages)
                or not isinstance(heading, str) or not heading.strip()
                or not isinstance(cat, str) or not cat):
            raise ValueError("invalid/duplicate offer identity, title or page")
        seen.add(oid)
        title = _normalized(heading)
        body = normalized_pages[page_no - 1]
        words = title.split()
        if len(title) < 7:
            state = "unresolved_short_heading"
        elif re.search(r"(?<!\w)" + re.escape(title) + r"(?!\w)", body):
            state = "page_title_correlated"
        elif len(words) >= 2 and all(word in body.split() for word in words):
            state = "page_tokens_only_review"
        else:
            state = "page_title_unresolved"
        results.append({
            "source_offer_id": oid,
            "source_catalog_id": cat,
            "source_catalog_page": page_no,
            "title_match_state": state,
            "price_badge_link": "unresolved",
            "member_discount_link": "unresolved",
            "bundle_condition_link": "unresolved",
            "source_status": "source_only",
            "rank_eligible": False,
        })
    return results


def count_states(records: Sequence[Mapping]) -> dict:
    """Safe summary; does not print private offer identities or prices."""
    counts = Counter(record["title_match_state"] for record in records)
    return {
        "api_offers": len(records),
        "direct_title_page": counts["page_title_correlated"],
        "tokens_review_only": counts["page_tokens_only_review"],
        "unresolved_short_heading": counts["unresolved_short_heading"],
        "unresolved_title_not_found": counts["page_title_unresolved"],
        "unresolved": (
            counts["unresolved_short_heading"] + counts["page_title_unresolved"]),
        "price_badges_attached": 0,
        "pdf_new_offers_recovered": 0,
        "checked_shop_prices": 0,
    }
