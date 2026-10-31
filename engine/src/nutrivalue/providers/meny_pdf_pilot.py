"""Offline, hash-pinned 10-offer PDF extraction pilot for one MENY week-42 flyer.

No HTTP, PDF publication, OCR, automated downloads, DB changes or verified rankings.
This exact flyer is user-provided, and extracted source candidates remain private.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PDF_SHA256 = '4178b086ca994bdf3cdda9ce89c2506023b53100ccedc620ddfe411a9b520f36'
VIEWER_URL = 'https://kundeavis.meny.no/'
EXPECTED_PAGES = 9
PDF_MAX_BYTES = 32_000_000
GEOMETRY = (666.142, 844.724)
Bbox = tuple[int, int, int, int]


class MenyPdfPilotError(ValueError):
    """The saved PDF or an extraction assertion differs from reviewed evidence."""


@dataclass(frozen=True)
class OfferSpec:
    key: str
    page: int
    heading: tuple[str, ...]
    heading_box: Bbox
    details_box: Bbox
    whole_box: Bbox
    cents_box: Bbox | None
    unit_box: Bbox | None
    price_basis: str
    offer_kind: str
    offer_label_box: Bbox
    marker: str


# Manually selected, one-flyer coordinate scopes. Do not reuse across weeks.
SPECS = (
    OfferSpec('nakkekoteletter', 1, ('FERSKE', 'NAKKEKOTELETTER'),
              (480, 213, 650, 266), (477, 333, 628, 367),
              (480, 229, 581, 351), (575, 239, 645, 317),
              (580, 300, 650, 329), 'per_kg', 'weekly',
              (469, 180, 601, 217), 'UKENS TILBUD'),
    OfferSpec('meierismor', 1, ('MEIERISMØR',),
              (318, 687, 396, 715), (322, 766, 435, 794),
              (322, 695, 391, 781), (387, 700, 439, 757),
              (389, 739, 432, 764), 'per_item', 'weekly',
              (318, 658, 406, 687), 'UKENS TILBUD'),
    OfferSpec('revet_ost', 2, ('Ekte revet ost',),
              (20, 249, 165, 271), (20, 267, 174, 303),
              (75, 169, 136, 240), (130, 173, 170, 225),
              (131, 205, 172, 229), 'per_package', 'weekly',
              (67, 148, 149, 170), 'UKENS TILBUD'),
    OfferSpec('karbonader', 2, ('Karbonader',),
              (176, 249, 318, 272), (176, 266, 324, 302),
              (233, 169, 295, 240), (283, 173, 325, 225),
              (285, 204, 327, 231), 'per_item', 'weekly',
              (223, 148, 306, 170), 'UKENS TILBUD'),
    OfferSpec('fiskesuppe', 2, ('Fiskesuppe',),
              (334, 249, 480, 272), (334, 266, 485, 294),
              (390, 169, 453, 240), (443, 173, 484, 225),
              (444, 205, 489, 230), 'per_package', 'weekly',
              (380, 148, 463, 170), 'UKENS TILBUD'),
    OfferSpec('peppes_pizza', 2, ('Peppes pizza',),
              (491, 249, 643, 272), (491, 266, 652, 307),
              (546, 169, 607, 240), (598, 173, 647, 225),
              (600, 205, 648, 230), 'per_item', 'weekly',
              (540, 148, 629, 170), 'UKENS TILBUD'),
    OfferSpec('bacalaofisk', 2, ('Bacalaofisk',),
              (20, 428, 173, 451), (20, 443, 173, 474),
              (65, 346, 139, 418), (130, 353, 172, 399),
              (131, 381, 173, 406), 'per_package', 'weekly',
              (58, 325, 139, 349), 'UKENS TILBUD'),
    OfferSpec('makrell_tomat', 2, ('Makrell i tomat',),
              (491, 426, 642, 449), (491, 442, 649, 472),
              (546, 345, 607, 419), (596, 350, 640, 401),
              (600, 382, 647, 406), 'per_item', 'weekly',
              (541, 325, 633, 347), 'UKENS TILBUD'),
    OfferSpec('egg_frittgaende', 3, ('Egg frittgående',),
              (178, 248, 319, 272), (178, 266, 326, 297),
              (231, 173, 295, 244), (284, 179, 328, 229),
              (285, 208, 331, 235), 'per_package',
              'ongoing_fixed_offer', (229, 147, 331, 171),
              'FASTE KNALLKJØP'),
    OfferSpec('oatly_3for2', 3, ('Oatly',),
              (334, 248, 469, 272), (334, 266, 469, 289),
              (388, 169, 489, 258), None, None,
              'unknown_no_single_price', 'ongoing_3_for_2',
              (385, 148, 482, 173), 'FASTE KNALLKJØP'),
)


class _PageText:
    """Decode costly PDF page dictionary just once for all matched zones."""

    def __init__(self, page):
        self._dict = page.get_text('dict')

    def get_text(self, fmt):
        if fmt != 'dict':
            raise MenyPdfPilotError('unexpected text read mode')
        return self._dict


def _spans(page, box: Bbox | None = None) -> list[dict]:
    unique = {}
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            for sp in line['spans']:
                value = ' '.join(sp['text'].split())
                if not value:
                    continue
                x0, y0, x1, y1 = sp['bbox']
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if box is not None and not (
                        box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
                    continue
                key = (value, *(round(v, 2) for v in sp['bbox']),
                       round(sp['size'], 2))
                unique[key] = dict(text=value, size=sp['size'],
                                   bbox=sp['bbox'])
    return sorted(unique.values(),
                  key=lambda sp: (round(sp['bbox'][1], 2), sp['bbox'][0]))


def _single_span(page, box, pattern, min_size, max_size, label):
    candidates = [sp['text'] for sp in _spans(page, box)
                  if min_size <= sp['size'] <= max_size
                  and re.fullmatch(pattern, sp['text'])]
    if len(candidates) != 1:
        raise MenyPdfPilotError(
            f'{label}: expected one glyph, found {candidates!r}')
    return candidates[0]


def _label(page, box, marker):
    if marker not in {sp['text'] for sp in _spans(page, box)}:
        raise MenyPdfPilotError(
            'expected offer label absent or moved: ' + marker)


def _detail_text(page, box):
    return ' '.join(sp['text'] for sp in _spans(page, box)
                    if 7.0 <= sp['size'] <= 13.5)


def _package_candidate(details):
    """Exact quantity only; never infer net mass for variants or approximate packs."""
    if re.search(r'\bca\.?\s*\d|\d+\s*[-/]\s*\d+\s*(?:g|ml|l|kg)\b',
                 details, re.I):
        return None, None, None
    found = re.findall(r'(?<!\w)(\d{1,4})\s*(kg|g|ml|l|stk)\b',
                       details, re.I)
    if len(found) != 1:
        return None, None, None
    qty, unit = found[0]
    amount = int(qty)
    if amount <= 0:
        return None, None, None
    unit = unit.lower()
    if unit == 'kg':
        return amount * 1000, 'g', None
    if unit == 'l':
        return amount * 1000, 'ml', None
    if unit == 'stk':
        return None, None, amount
    return amount, unit, None


def extract_offer(page, spec: OfferSpec) -> dict:
    titles = [sp['text'] for sp in _spans(page, spec.heading_box)
              if 9.7 <= sp['size'] <= 18.2]
    if titles != list(spec.heading):
        raise MenyPdfPilotError(
            f'{spec.key}: heading moved/ambiguous: {titles!r}')
    details = _detail_text(page, spec.details_box)
    if not details:
        raise MenyPdfPilotError(spec.key + ': missing detail evidence')
    _label(page, spec.offer_label_box, spec.marker)
    if spec.cents_box is not None:
        major = _single_span(page, spec.whole_box, r'[1-9]\d{0,3}',
                             45, 110, spec.key + ': major')
        minor = _single_span(page, spec.cents_box, r'\d{2}',
                             25, 65, spec.key + ': minor')
        assert spec.unit_box is not None
        unit = _single_span(page, spec.unit_box,
                            r'/\s*(?:KG|PK|STK)', 10, 19,
                            spec.key + ': unit')
        source_unit = unit.strip().replace(' ', '').lstrip('/').lower()
        units = {'per_kg': 'kg', 'per_package': 'pk', 'per_item': 'stk'}
        if source_unit != units[spec.price_basis]:
            raise MenyPdfPilotError(
                spec.key + ': displayed unit differs from scoped price_basis')
        price_ore = int(major) * 100 + int(minor)
        if not 0 < price_ore <= 100_000:
            raise MenyPdfPilotError(spec.key + ': unexpected price range')
        condition = None
    else:
        # PyMuPDF versions can split badge glyphs ('3', '2', 'FOR')
        # differently or move their text bounding boxes by a few
        # points. Verify the exact *local* offer region semantically;
        # do not silently turn "3 for 2" into a price in øre.
        badge_parts = [sp['text'] for sp in _spans(page, spec.whole_box)]
        badge = ' '.join(badge_parts)
        badge_markers = (
            re.search(r'\b3\s*(?:FOR\s*)?2\b', badge, re.I),
            re.search(r'\bFOR\b', badge, re.I),
            re.search(r'\bPLUKK\s*&\s*MIKS\b', badge, re.I),
            re.search(r'\b33\s*%\s*RABATT\s+VED\s+KJØP\s+AV\s+3\b',
                      badge, re.I),
        )
        if not all(badge_markers):
            raise MenyPdfPilotError(
                f'{spec.key}: incomplete 3-for-2 source badge; '
                f'observed={badge_parts[:12]!r}')
        price_ore = None
        source_unit = None
        condition = 'buy_3_pay_for_2_no_base_price'
    qty, pack_unit, count = _package_candidate(details)
    return {
        'key': spec.key,
        'source_page': spec.page,
        'source_name': ' '.join(titles),
        'source_details': details,
        'display_price_ore': price_ore,
        'source_price_unit': source_unit,
        'price_basis': spec.price_basis,
        'offer_kind': spec.offer_kind,
        'conditional_deal': condition,
        'package_quantity_candidate': qty,
        'package_unit_candidate': pack_unit,
        'pack_count_candidate': count,
        'review_state': 'source_only',
        'visual_item_check': 'pending_independent_review',
        'store_price_verification': 'none',
        'ean_verification': 'none',
        'nutrition_verification': 'none',
    }


def extract_pdf(payload: bytes) -> dict:
    if not payload.startswith(b'%PDF-') or len(payload) > PDF_MAX_BYTES:
        raise MenyPdfPilotError('invalid or oversized local PDF')
    digest = hashlib.sha256(payload).hexdigest()
    if digest != PDF_SHA256:
        raise MenyPdfPilotError(
            'PDF checksum changed: reviewed layout no longer applies')
    try:
        import pymupdf  # optional local dependency, not required in core CI
    except ImportError as exc:
        raise MenyPdfPilotError(
            'install pymupdf into a private venv first') from exc
    try:
        with pymupdf.open(stream=payload, filetype='pdf') as doc:
            if doc.is_encrypted or len(doc) != EXPECTED_PAGES:
                raise MenyPdfPilotError('page count/encryption mismatch')
            if any(abs(p.rect.width - GEOMETRY[0]) > 2 or
                   abs(p.rect.height - GEOMETRY[1]) > 2 for p in doc):
                raise MenyPdfPilotError('unexpected page geometry')
            heading = doc[0].get_text()
            footer = doc[3].get_text()
            if ('MANDAG 12. OKTOBER - LØRDAG 17. OKTOBER' not in heading
                    or 'UKE 42, 2026' not in heading):
                raise MenyPdfPilotError('week-42 heading mismatch')
            if ('Kampanjeperiode: Mandag 12.10. – Lørdag 17.10.2026'
                    not in footer):
                raise MenyPdfPilotError('explicit flyer validity period missing')
            pages = {i: _PageText(doc[i - 1]) for i in
                     {spec.page for spec in SPECS}}
            cards = [extract_offer(pages[spec.page], spec) for spec in SPECS]
    except (RuntimeError, ValueError) as exc:
        if isinstance(exc, MenyPdfPilotError):
            raise
        raise MenyPdfPilotError(f'PDF parsing failed: {exc}') from exc
    if len(cards) != 10 or len({card['key'] for card in cards}) != 10:
        raise MenyPdfPilotError('expected ten unique source-only offers')
    return {
        'source': 'meny_week42_monday_pdf_offline_pilot',
        'viewer_url': VIEWER_URL,
        'direct_pdf_url': 'not_captured',
        'pdf_sha256': digest,
        'page_count': EXPECTED_PAGES,
        'catalogue_window': '2026-10-12/2026-10-17',
        'offer_validity': 'per_offer_review_required',
        'local_store_scope': 'not_verified',
        'price_rights_reuse': 'not_verified',
        'extracted_at_utc': datetime.now(timezone.utc).isoformat(),
        'cards': cards,
        'verified_protein_value_rows': 0,
    }


def save_private(pdf_path: Path, out_path: Path, private_root: Path) -> dict:
    root = private_root.resolve()
    source = pdf_path.resolve()
    out = out_path.resolve()
    if (source == out or not source.is_relative_to(root)
            or not out.is_relative_to(root)):
        raise MenyPdfPilotError(
            'both PDF and report must be separate files below data/private')
    if not source.is_file() or source.stat().st_size > PDF_MAX_BYTES:
        raise MenyPdfPilotError('local PDF missing or oversized')
    if out.exists():
        raise MenyPdfPilotError(
            'private report exists: do not overwrite evidence')
    result = extract_pdf(source.read_bytes())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')
    return result
