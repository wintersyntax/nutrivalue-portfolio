"""TASK-033: offline, review-only geometry evidence for multibuy flyer badges.

Uses Poppler's pdftotext -bbox-layout XHTML (not OCR). Strict source page,
single title line, one-to-one badge assignment and matching displayed total
are needed even for a *candidate*. Never asserts checkout/rank eligibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
import re
from typing import Mapping, Sequence
from xml.etree import ElementTree as ET

from nutrivalue.providers.tjek_pdf_link import _normalized

MAX_BBOX_BYTES = 10_000_000
MULTIBUY_LINE = re.compile(r"^([2-9]|1[0-2])\s+for$", re.I)
INT_WORD = re.compile(r"^[0-9]{1,4}$")
CENT_WORD = re.compile(r"^[0-9]{2}$")


@dataclass(frozen=True)
class Box:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Page:
    width: float
    height: float
    lines: tuple[Box, ...]
    words: tuple[Box, ...]


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _float(node: ET.Element, attribute: str) -> float:
    value = float(node.attrib[attribute])
    if not isfinite(value):
        raise ValueError("nonfinite PDF bbox coordinate")
    return value


def _box(node: ET.Element, text: str, width: float, height: float) -> Box:
    x0, y0, x1, y1 = (_float(node, name)
                       for name in ("xMin", "yMin", "xMax", "yMax"))
    if not (0 <= x0 < x1 <= width + 0.1 and
            0 <= y0 < y1 <= height + 0.1):
        raise ValueError("invalid/out-of-page PDF bbox")
    return Box(text, x0, y0, x1, y1)


def parse_bbox_pages(raw: bytes) -> list[Page]:
    """Return all physical PDF pages (including empty). No external DTD reads."""
    if not raw or len(raw) > MAX_BBOX_BYTES or b"<!ENTITY" in raw.upper():
        raise ValueError("empty, oversized, or entity-bearing PDF bbox markup")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("invalid PDF bbox XML") from exc
    pages: list[Page] = []
    for node in root.iter():
        if _tag(node) != "page":
            continue
        width = _float(node, "width")
        height = _float(node, "height")
        if not (0 < width <= 10000 and 0 < height <= 10000):
            raise ValueError("invalid PDF page size")
        lines: list[Box] = []
        words: list[Box] = []
        for line in node.iter():
            if _tag(line) != "line":
                continue
            children = [w for w in line if _tag(w) == "word" and (w.text or "").strip()]
            if not children:
                continue
            if len(children) > 250:
                raise ValueError("unbounded PDF line length")
            wboxes = [_box(w, w.text or "", width, height) for w in children]
            words.extend(wboxes)
            lines.append(_box(line, " ".join(w.text or "" for w in children),
                              width, height))
        if len(words) > 50000 or len(lines) > 15000:
            raise ValueError("unbounded PDF page contents")
        pages.append(Page(width, height, tuple(lines), tuple(words)))
        if len(pages) > 300:
            raise ValueError("excessive PDF page count")
    if not pages:
        raise ValueError("PDF bbox XML missing pages")
    return pages


def _ore(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        # Printed Norwegian price labels use comma as a decimal separator.
        # Numeric API prices already use a point; do not round either form.
        amount = Decimal(str(value).replace(",", "."))
        cents = amount * 100
        if (not amount.is_finite() or amount <= 0 or
                cents != cents.to_integral_value() or cents > 10_000_000):
            return None
        return int(cents)
    except (TypeError, ValueError, InvalidOperation):
        return None


def _badge_total_observed(page: Page, banner: Box, title: Box, ore: int) -> bool:
    """Large whole+small cents glyphs printed directly under this banner.

    Original API price must equal the FULL multibuy badge total. This is
    geometry corroboration only, not membership, variant or shop proof.
    """
    whole, cents = divmod(ore, 100)
    integers = [
        w for w in page.words
        if INT_WORD.fullmatch(w.text) and int(w.text) == whole
        # Large flyer price glyphs often overlap the '2 for' line bbox.
        and -40 <= w.y0 - banner.y1 <= 105
        and w.cy < title.y0 and abs(w.cx - banner.cx) <= 135
        and (w.y1 - w.y0) >= 24
    ]
    for integer in integers:
        for cent in page.words:
            if (CENT_WORD.fullmatch(cent.text)
                    and int(cent.text) == cents
                    and integer.x1 - 10 <= cent.x0 <= integer.x1 + 95
                    and abs(cent.cy - integer.cy) <= 65
                    and cent.cy < title.y0):
                return True
    return False


def match_multibuy_badges(offers: Sequence[Mapping],
                          pages: Sequence[Page]) -> list[dict]:
    """Produce *candidate* badges with exact product/page/total evidence.

    If two products compete for a banner, a product competes for two badges,
    or the original total cannot be corroborated, mark review-only.
    Matching is deliberately limited to a single PDF title line. PDF-only
    offers and percentage group promotions are NOT inferred.
    """
    if not pages:
        raise ValueError("missing PDF geometry")
    by_page: dict[int, list[tuple[int, Mapping, Box]]] = {}
    result: list[dict] = []
    used_ids: set[str] = set()
    for offer in offers:
        if not isinstance(offer, Mapping):
            raise ValueError("malformed offer record")
        oid = offer.get("id")
        page_number = offer.get("catalog_page")
        title = offer.get("heading")
        if (not isinstance(oid, str) or not oid or oid in used_ids or
                type(page_number) is not int or not 1 <= page_number <= len(pages)
                or not isinstance(title, str) or not title.strip()):
            raise ValueError("invalid/duplicate offer identity or PDF page")
        used_ids.add(oid)
        report = {
            "source_offer_id": oid,
            "source_catalog_page": page_number,
            "badge_state": "no_unique_geometry",
            "bundle_units_candidate": None,
            "source_total_recorroborated": False,
            "source_status": "source_only",
            "rank_eligible": False,
        }
        index = len(result)
        result.append(report)
        norm = _normalized(title)
        if len(norm) < 7:
            continue
        title_matches = [
            line for line in pages[page_number - 1].lines
            if re.search(r"(?<!\w)" + re.escape(norm) + r"(?!\w)",
                         _normalized(line.text))
        ]
        if len(title_matches) != 1:
            continue
        by_page.setdefault(page_number, []).append((index, offer, title_matches[0]))

    for page_number, candidates in by_page.items():
        page = pages[page_number - 1]
        banners = [
            (line, int(match.group(1)))
            for line in page.lines
            if (match := MULTIBUY_LINE.fullmatch(_normalized(line.text)))
        ]
        near: dict[int, list[tuple[int, float]]] = {}
        by_offer: dict[int, list[tuple[int, float]]] = {}
        for b_idx, (banner, _units) in enumerate(banners):
            for offer_idx, _offer, title in candidates:
                dy = title.y0 - banner.y1
                dx = abs(title.cx - banner.cx)
                if 10 <= dy <= 230 and dx <= min(115, page.width * 0.16):
                    distance = dy + dx * 0.5
                    near.setdefault(b_idx, []).append((offer_idx, distance))
                    by_offer.setdefault(offer_idx, []).append((b_idx, distance))
        for b_idx, eligible in near.items():
            # Fail closed: if several titles are near the same badge, do
            # not guess which card's price/tier it represents.
            if len(eligible) != 1:
                continue
            offer_idx, _ = eligible[0]
            if len(by_offer.get(offer_idx, ())) != 1:
                continue
            _idx, offer, title = next(
                row for row in candidates if row[0] == offer_idx)
            banner, units = banners[b_idx]
            price = offer.get("pricing")
            ore = _ore(price.get("price")) if isinstance(price, Mapping) else None
            if ore is None:
                continue
            if _badge_total_observed(page, banner, title, ore):
                result[offer_idx].update({
                    "badge_state": "geometry_total_correlated_review_only",
                    "bundle_units_candidate": units,
                    "source_total_recorroborated": True,
                })
            else:
                result[offer_idx]["badge_state"] = "geometry_without_total_review"
    return result


def summarize_badge_candidates(rows: Sequence[Mapping]) -> dict[str, int]:
    from collections import Counter

    counts = Counter(r["badge_state"] for r in rows)
    return {
        "offers_inspected": len(rows),
        "bundle_total_geometry_correlated_review_only":
            counts["geometry_total_correlated_review_only"],
        "possible_banner_without_matching_total":
            counts["geometry_without_total_review"],
        "other_or_unresolved_geometry": counts["no_unique_geometry"],
        "price_became_rank_eligible": 0,
        "shelf_prices_verified": 0,
    }
