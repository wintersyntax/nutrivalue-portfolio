"""Fail-closed, offline 10-card coordinate pilot for one owner-supplied Coop Prix PDF.

No network, OCR, automatic SKU/price verification, price ranking or database writes.
The original retailer PDF must remain private. Coordinates are PDF points; the
hash-pinned layout is intentionally NOT a reusable catalogue-wide crawler.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SOURCE_URL = 'https://kundeavis.coop.no/aviser/?id=2651&r=1'
SOURCE_SHA256 = '22051e42dd6891d1a9d2482e7a1b2c66b16f02297f101a2d3b2939aff10a4c29'
EXPECTED_PAGES = 20
MAX_BYTES = 16_000_000
Box = tuple[int, int, int, int]


class CoopPdfPilotError(ValueError):
    """Source changed or unambiguous visual/text association is unavailable."""


@dataclass(frozen=True)
class CardSpec:
    key: str
    page: int
    title_parts: tuple[str, ...]
    title_area: Box
    details_area: Box
    main_area: Box
    cents_area: Box | None
    price_basis: str
    offer_kind: str
    must_see: tuple[str, ...] = ()


# Ten manually located *zones*, not assertions of current store availability.
# No source PDF, copied images or frozen actual offer prices live in Git.
CARDS = (
    CardSpec('norvegia_duo', 1, ('NORVEGIA DUO',), (452, 320, 532, 342),
             (450, 332, 566, 382), (433, 146, 569, 248), None,
             'per_kg', 'limited_stock', ('PARTIVARE', 'PR KG')),
    CardSpec('freia_storplater', 1, ('FREIA STORPLATER',), (200, 790, 321, 808),
             (198, 803, 322, 832), (210, 685, 279, 755), None,
             'per_bundle', 'member_multibuy', ('3 for', 'Ikke-medlem?')),
    CardSpec('chicken_ready', 2, ('STEKT OG SPISEKLAR', 'KYLLINGILET'),
             (476, 496, 607, 526), (477, 521, 607, 559),
             (498, 381, 551, 447), (548, 388, 578, 420),
             'per_package', 'regular'),
    CardSpec('gilde_nakkekoteletter', 2, ('GILDE NAKKEKOTELETTER',),
             (436, 758, 576, 778), (436, 772, 571, 791),
             (454, 645, 535, 715), None, 'per_kg', 'regular', ('PR KG',)),
    CardSpec('sopps_spaghetti', 3, ('SOPPS SPAGHETTI',),
             (432, 359, 530, 381), (420, 372, 546, 401),
             (537, 163, 589, 230), (580, 169, 607, 203),
             'per_package', 'regular'),
    CardSpec('coop_brokkoliblanding', 3, ('COOP BROKKOLIBLANDING',),
             (118, 787, 243, 807), (107, 800, 263, 832),
             (230, 480, 285, 547), (272, 487, 302, 519),
             'per_package', 'regular'),
    CardSpec('coop_superbrod', 8, ('COOP SUPERBRØD',),
             (497, 348, 590, 362), (493, 359, 592, 395),
             (484, 176, 573, 286), (567, 187, 610, 237),
             'per_package', 'fixed_offer', ('FAST TILBUD',)),
    CardSpec('maxim_proteinbar', 9, ('MAXIM PROTEINBAR',),
             (239, 539, 335, 558), (208, 550, 370, 580),
             (534, 444, 590, 514), (583, 454, 612, 486),
             'per_package', 'regular'),
    CardSpec('coop_frokostegg', 12, ('COOP FROKOSTEGG 12 PK',),
             (217, 647, 438, 681), (234, 673, 410, 711),
             (394, 466, 514, 609), (508, 480, 562, 545),
             'per_package', 'member_only', ('MEDLEMSPRIS', 'Ikke medlem?')),
    CardSpec('coop_morgenstykker', 13, ('COOP MORGENSTYKKER 8 PK',),
             (201, 647, 449, 681), (251, 673, 405, 699),
             (394, 466, 514, 609), (506, 480, 562, 545),
             'per_package', 'member_only', ('MEDLEMSPRIS', 'Ikke medlem?')),
)


def _inside(bbox: tuple[float, float, float, float], area: Box) -> bool:
    midx = (bbox[0] + bbox[2]) / 2
    midy = (bbox[1] + bbox[3]) / 2
    return area[0] <= midx <= area[2] and area[1] <= midy <= area[3]


def _spans(page: object, area: Box | None = None) -> list[dict]:
    output = []
    seen = set()
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            for s in line['spans']:
                value = ' '.join(s['text'].split())
                if not value or (area is not None and not _inside(s['bbox'], area)):
                    continue
                key = (value, *(round(z, 1) for z in s['bbox']), round(s['size'], 1))
                if key in seen:
                    continue
                seen.add(key)
                output.append(dict(text=value, size=s['size'], bbox=s['bbox']))
    return sorted(output, key=lambda s: (round(s['bbox'][1] / 3), s['bbox'][0]))


def _one_price(page: object, area: Box, *, cents: bool) -> int:
    entries = [s['text'] for s in _spans(page, area)
               if s['size'] >= 20 and re.fullmatch(r'\d{1,4}(?:,-)?', s['text'])]
    if len(entries) != 1:
        raise CoopPdfPilotError(f'ambiguous {"cents" if cents else "major"} price glyphs: {entries!r}')
    number = entries[0]
    if cents and not re.fullmatch(r'\d{2}', number):
        raise CoopPdfPilotError('invalid two-digit fraction')
    if not cents and (number.endswith(',-') and not re.fullmatch(r'\d{1,4},-', number)):
        raise CoopPdfPilotError('invalid whole-krone badge')
    return int(number.removesuffix(',-'))


def _package_grams(details: str) -> int | None:
    if re.search(r'\bca\.|\bfra\s+\d|\d+\s*[-/]\s*\d+\s*g\b', details, re.I):
        return None
    values = re.findall(r'(?<!\d)(\d{1,4})\s*g\b', details, re.I)
    return int(values[0]) if len(values) == 1 and int(values[0]) > 0 else None


def extract_card(page: object, spec: CardSpec) -> dict:
    titles = [s['text'] for s in _spans(page, spec.title_area) if s['size'] >= 11]
    title = ' '.join(part for part in spec.title_parts)
    if titles != list(spec.title_parts):
        raise CoopPdfPilotError(f'{spec.key}: expected title segments {spec.title_parts!r}, got {titles!r}')
    details = ' '.join(s['text'] for s in _spans(page, spec.details_area)
                       if 6 <= s['size'] <= 18)
    if not details:
        raise CoopPdfPilotError(f'{spec.key}: missing product details')
    full_visible = ' '.join(s['text'] for s in _spans(page))
    for marker in spec.must_see:
        if marker.casefold() not in full_visible.casefold():
            raise CoopPdfPilotError(f'{spec.key}: missing offer condition {marker!r}')
    whole = _one_price(page, spec.main_area, cents=False)
    cents = _one_price(page, spec.cents_area, cents=True) if spec.cents_area else 0
    if spec.cents_area is None and not any(
        t in (f'{whole},-', str(whole)) for t in
        [s['text'] for s in _spans(page, spec.main_area)]
    ):
        raise CoopPdfPilotError(f'{spec.key}: missing whole-krone badge')
    price_ore = whole * 100 + cents
    if not 0 < price_ore <= 100_000:
        raise CoopPdfPilotError(f'{spec.key}: out-of-range price')
    grams = _package_grams(details)
    if spec.price_basis != 'per_package':
        grams = None if 'ca.' in details.lower() or spec.price_basis == 'per_bundle' else grams
    return {
        'key': spec.key, 'source_page': spec.page, 'source_title': title,
        'source_details': details, 'display_price_ore': price_ore,
        'price_basis': spec.price_basis,
        'offer_kind': spec.offer_kind, 'package_grams_candidate': grams,
        'review_state': 'source_only', 'item_price_visual_check': 'pending_owner_review',
        'independent_store_price_verification': 'none',
        'exact_ean_verification': 'none', 'nutrition_verification': 'none',
    }


def extract_pdf(payload: bytes) -> dict:
    if len(payload) > MAX_BYTES or not payload.startswith(b'%PDF-'):
        raise CoopPdfPilotError('invalid or oversized local PDF')
    digest = hashlib.sha256(payload).hexdigest()
    if digest != SOURCE_SHA256:
        raise CoopPdfPilotError('PDF checksum changed: coordinates untrusted; stop and review a new flyer')
    try:
        import fitz  # optional owner-local PyMuPDF; not a CI dependency
    except ImportError as exc:
        raise CoopPdfPilotError('install pymupdf into a private venv first') from exc
    try:
        with fitz.open(stream=payload, filetype='pdf') as doc:
            if doc.is_encrypted or len(doc) != EXPECTED_PAGES:
                raise CoopPdfPilotError('unexpected PDF page count or encryption')
            if any(abs(p.rect.width - 651.969) > 2 or
                   abs(p.rect.height - 850.394) > 2 for p in doc):
                raise CoopPdfPilotError('unexpected page geometry')
            footnote = doc[8].get_text()
            mixed_week_footnote = ('uke 42' in footnote.lower()
                                   and '25.10.26' in footnote)
            if not mixed_week_footnote:
                raise CoopPdfPilotError('known week-42/43 footnote ambiguity disappeared')
            items = [extract_card(doc[spec.page - 1], spec) for spec in CARDS]
    except (RuntimeError, ValueError) as exc:
        if isinstance(exc, CoopPdfPilotError):
            raise
        raise CoopPdfPilotError(f'PDF read failed: {exc}') from exc
    if len(items) != 10 or len({x['key'] for x in items}) != 10:
        raise CoopPdfPilotError('expected ten distinct reviewed layout scopes')
    return {
        'source': 'coop_prix_felles_pdf_offline_pilot',
        'source_url': SOURCE_URL,
        'pdf_sha256': digest, 'pdf_pages': EXPECTED_PAGES,
        'catalog_scope': 'coop_prix_felles__not_a_named_store',
        'viewer_window_owner_observed': '2026-10-12/2026-10-18',
        # The viewer presents 12–18 Oct (ISO week 42), while page 9's
        # small-print line says "uke 42" and Sunday 25 Oct (ISO week 43).
        # This may be a later-week offer or a print error: not grounds
        # to reject the entire flyer, nor permission to extend all offers.
        'pdf_footer_end_date_note': 'page_9_mentions_sunday_2026-10-25_week_43',
        'offer_validity_verification': 'per_offer_review_needed_mixed_week_note',
        'rights_and_reuse_verification': 'not_completed',
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'cards': items,
        'verified_retail_value_rows': 0,
    }


def save_private(pdf_path: Path, output_path: Path, private_root: Path) -> dict:
    root = private_root.resolve()
    source = pdf_path.resolve()
    dest = output_path.resolve()
    if not source.is_relative_to(root) or not dest.is_relative_to(root) or source == dest:
        raise CoopPdfPilotError('both PDF and report must be below data/private, in separate files')
    if not source.is_file():
        raise CoopPdfPilotError('private PDF file does not exist')
    if source.stat().st_size > MAX_BYTES:
        raise CoopPdfPilotError('oversized local PDF')
    result = extract_pdf(source.read_bytes())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise CoopPdfPilotError('output report exists; never overwrite private evidence')
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result
