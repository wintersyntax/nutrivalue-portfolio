"""Bounded first-party Oatly PDF campaign -> individual MENY product price evidence.

Read previously saved, immutable public HTML; never fetch from this module.
Product-card base prices must come from their own first-party page, not the
group PDF. All links remain source_only with local store and checkout unverified.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nutrivalue.providers.meny_pdf_pilot import PDF_SHA256
from nutrivalue.providers.source_probe import (
    MAX_HTML_BYTES, SOURCES, SourceProbeError, _VisibleHTML, validate_url,
)

CAMPAIGN_URL = 'https://meny.no/varer/kampanjer/plukk-miks-oatly-produkter-129704'
MAX_REPORT_BYTES = 200_000


class OatlyLinkError(ValueError):
    """Fail closed when identity, badge, amount or source provenance is ambiguous."""


@dataclass(frozen=True)
class Target:
    source: str
    title_fragment: str
    product_label: str
    volume_ml: int


TARGETS = (
    Target('meny_oatly_barista_500', 'Barista Edition 0.5l Oatly',
           'Oatly Barista Edition 0.5l', 500),
    Target('meny_oatly_vanilla_1000', 'Vanilla 1l Oatly',
           'Oatly iKaffe Vanilla 1l', 1000),
    Target('meny_oatly_churros_1000', 'Churros Flavour 1l Oatly',
           'Oatly iKaffe Churros 1l', 1000),
    Target('meny_oatly_chocolate_250', 'Sjokolade 250ml Oatly',
           'Oatly chocolate 250ml', 250),
)
TARGET_INDEX = {target.source: target for target in TARGETS}


def _utc_stamp(value: object) -> str:
    if not isinstance(value, str):
        raise OatlyLinkError('missing captured-at UTC timestamp')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise OatlyLinkError('invalid captured-at timestamp') from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise OatlyLinkError('naive captured-at timestamp')
    return dt.isoformat(timespec='seconds')


def _price_from_title(title: str, fragment: str) -> int:
    if fragment.casefold() not in title.casefold() or 'Oatly' not in title:
        raise OatlyLinkError('exact Oatly product identity missing from page title')
    prices = re.findall(r'-\s*(\d{1,4}),(\d{2})\s*kr\s*\|\s*MENY\b',
                        title, re.I)
    if len(prices) != 1:
        raise OatlyLinkError('expected one numeric kr price in exact product title')
    price = int(prices[0][0]) * 100 + int(prices[0][1])
    if not 0 < price <= 100_000:
        raise OatlyLinkError('out-of-range product base price')
    return price


def analyze_product(source: str, payload: bytes, *,
                    captured_at: str, retrieval: str) -> dict:
    if source not in TARGET_INDEX:
        raise OatlyLinkError('unknown pinned Oatly product source')
    if retrieval not in ('public_http', 'saved_html', 'playwright_render'):
        raise OatlyLinkError('unsupported capture provenance')
    if not payload or len(payload) > MAX_HTML_BYTES:
        raise OatlyLinkError('missing/oversized HTML')
    try:
        html = payload.decode('utf-8-sig')
    except UnicodeError as exc:
        raise OatlyLinkError('HTML must be UTF-8') from exc
    if '<html' not in html.lower():
        raise OatlyLinkError('not an HTML document')
    parsed = _VisibleHTML()
    parsed.feed(html)
    parsed.close()
    target = TARGET_INDEX[source]
    title_price = _price_from_title(parsed.title, target.title_fragment)
    visible = ' '.join(parsed.visible)
    norm = ' '.join(visible.split())
    if not all(marker.casefold() in norm.casefold() for marker in (
            target.title_fragment, '3 for 2',
            'Faste knallkjøp', 'Plukk & miks Oatly produkter')):
        raise OatlyLinkError('product name or campaign eligibility not visible')
    # Price of the *first primary product*, not recommendations below
    # "Om produktet". A browser may serve only a Next.js loading skeleton;
    # that must remain unpriced even if <title> contains a price.
    pos = norm.casefold().find(target.title_fragment.casefold())
    section = norm[pos:]
    end = section.casefold().find('om produktet')
    if end == -1 or end > 750:
        raise OatlyLinkError('cannot isolate first product header from recommendations')
    section = section[:end]
    # Superscript prices may be separate text nodes (29 90 kr) or 29,90 kr.
    prices = re.findall(r'(?<!\d)(\d{1,4})\s*[, ]\s*(\d{2})\s*kr\b',
                        section, re.I)
    if not prices:
        raise OatlyLinkError('base price not present in first visible product header')
    visible_price = int(prices[0][0]) * 100 + int(prices[0][1])
    if visible_price != title_price:
        raise OatlyLinkError('visible primary product price differs from page title')
    # Cross-check the volume-specific displayed unit price, not just
    # the SKU heading: a 0.5-l package is not interchangeable with 1 l.
    unit_values = re.findall(r'(?<!\d)(\d{1,4}),\s*(\d{2})\s*kr/l\b',
                             section, re.I)
    if len(unit_values) != 1:
        raise OatlyLinkError('missing or ambiguous kr/l price in main header')
    unit_ore = int(unit_values[0][0]) * 100 + int(unit_values[0][1])
    numerator = title_price * 1000
    if numerator % target.volume_ml or unit_ore != numerator // target.volume_ml:
        raise OatlyLinkError('unit price contradicts labelled pack size')
    return {
        'source': source,
        'source_url': SOURCES[source].url,
        'source_sha256': hashlib.sha256(payload).hexdigest(),
        'retrieval': retrieval,
        'captured_at': _utc_stamp(captured_at),
        'product_label_candidate': target.product_label,
        'retailer_url_product_key_not_verified_ean':
            SOURCES[source].url.rsplit('-', 1)[-1],
        'net_volume_ml_from_page_label': target.volume_ml,
        'base_price_ore_source_only': title_price,
        'price_evidence': 'same_first_party_title_and_visible_primary_header',
        'campaign_label': 'Plukk & miks Oatly produkter',
        'campaign_badge': '3 for 2',
        'store_displayed_in_retailer_ui': (
            'MENY Bryn' if re.search(r'\bMENY\s+Bryn\b', norm, re.I)
            else None),
        'store_scope': 'not_selected_or_verified',
        'eligibility_review': 'not_checkout_verified',
        'nutrition_basis_review': 'not_verified',
        'review_state': 'source_only',
    }


def _load_pdf_anchor(pdf_report: Path) -> dict:
    if not pdf_report.is_file() or pdf_report.stat().st_size > MAX_REPORT_BYTES:
        raise OatlyLinkError('private MENY PDF report missing or oversized')
    try:
        report = json.loads(pdf_report.read_text(encoding='utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise OatlyLinkError('invalid private PDF report') from exc
    if not isinstance(report, dict) or report.get('pdf_sha256') != PDF_SHA256:
        raise OatlyLinkError('wrong MENY PDF source SHA')
    if report.get('catalogue_window') != '2026-10-12/2026-10-17':
        raise OatlyLinkError('unexpected PDF campaign window')
    cards = report.get('cards')
    if not isinstance(cards, list):
        raise OatlyLinkError('PDF cards missing')
    oat = [card for card in cards
           if isinstance(card, dict) and card.get('key') == 'oatly_3for2']
    if (len(oat) != 1 or oat[0].get('source_page') != 3
            or oat[0].get('source_name') != 'Oatly'
            or oat[0].get('display_price_ore') is not None
            or oat[0].get('conditional_deal')
            != 'buy_3_pay_for_2_no_base_price'
            or oat[0].get('offer_kind') != 'ongoing_3_for_2'
            or oat[0].get('review_state') != 'source_only'):
        raise OatlyLinkError('PDF Oatly campaign anchor is incomplete/changed')
    return report


def _load_snapshot(source: str, root: Path) -> dict:
    folder = root / source
    if not folder.is_dir() or not folder.resolve().is_relative_to(root.resolve()):
        raise OatlyLinkError(f'{source}: no safe private first-party HTML snapshot')
    reports = sorted(folder.glob('*.json'))
    if len(reports) != 1:
        raise OatlyLinkError(f'{source}: expected exactly one snapshot; select manually')
    path = reports[0]
    if not path.resolve().is_relative_to(root.resolve()):
        raise OatlyLinkError('source report points outside private storage')
    if not re.fullmatch(r'[0-9a-f]{64}', path.stem):
        raise OatlyLinkError('snapshot filename must be a SHA-256 digest')
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise OatlyLinkError('oversized source snapshot metadata')
    try:
        info = json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise OatlyLinkError('invalid source report JSON') from exc
    if not isinstance(info, dict):
        raise OatlyLinkError('unexpected source metadata shape')
    if (info.get('source') != source
            or info.get('sha256') != path.stem
            or info.get('source_url') != SOURCES[source].url
            or info.get('retrieval') != 'public_http'):
        raise OatlyLinkError('metadata is not a matching public first-party fetch')
    try:
        validate_url(SOURCES[source], info.get('final_url', ''))
    except (SourceProbeError, TypeError) as exc:
        raise OatlyLinkError('unsafe redirected product snapshot') from exc
    source_html = folder / (path.stem + '.html')
    if not source_html.resolve().is_relative_to(root.resolve()):
        raise OatlyLinkError('source HTML points outside private storage')
    if not source_html.is_file() or source_html.stat().st_size > MAX_HTML_BYTES:
        raise OatlyLinkError('missing or oversized private HTML snapshot')
    raw = source_html.read_bytes()
    if hashlib.sha256(raw).hexdigest() != path.stem:
        raise OatlyLinkError('source HTML digest mismatch')
    return analyze_product(
        source, raw, captured_at=info.get('fetched_at'),
        retrieval=info.get('retrieval'))


def _load_render_snapshot(report_path: Path, private_root: Path) -> dict:
    """Only accept the owner's original one-shot Barista Chromium evidence.

    Browser HTML is NOT a public_http snapshot and must have screenshot,
    exact URL, timestamp and hash records. No browser launch/network here.
    """
    root = private_root.resolve()
    expected_dir = root / 'render-probes/meny-oatly-barista'
    if report_path.resolve() != expected_dir / 'report.json':
        raise OatlyLinkError('browser report must be the pinned private Barista probe')
    if (not report_path.is_file() or
            report_path.stat().st_size > MAX_REPORT_BYTES):
        raise OatlyLinkError('missing or oversized private Chromium report')
    try:
        info = json.loads(report_path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise OatlyLinkError('invalid Chromium report JSON') from exc
    if not isinstance(info, dict):
        raise OatlyLinkError('unexpected Chromium report shape')
    url = SOURCES['meny_oatly_barista_500'].url
    flags = info.get('signals')
    if (info.get('source') != 'meny_oatly_barista_500'
            or info.get('requested_url') != url
            or info.get('final_url') != url
            or info.get('http_status') != 200
            or info.get('method')
            != 'one_browser_navigation_six_second_wait_no_clicks'
            or info.get('evidence_state')
            != 'browser_render_only_needs_owner_visual_review'
            or not isinstance(flags, dict)
            or any(flags.get(x) is not True for x in (
                'visible_mentions_oatly', 'visible_mentions_29_90',
                'visible_mentions_campaign', 'visible_mentions_price_unit'))
            or flags.get('visible_mentions_loading') is not False
            or flags.get('verified_price') is not False
            or flags.get('store_selected') is not False
            or flags.get('product_campaign_price_join') != 'not_attempted'):
        raise OatlyLinkError('Chromium capture does not show required first-party price evidence')
    captured_at = _utc_stamp(info.get('captured_at_utc'))
    if (not isinstance(info.get('title'), str)
            or not isinstance(info.get('rendered_html_sha256'), str)
            or not isinstance(info.get('screenshot_sha256'), str)):
        raise OatlyLinkError('missing browser report hashes or title')
    html_path = expected_dir / 'rendered.html'
    image_path = expected_dir / 'screenshot.png'
    if any(not path.resolve().is_relative_to(root) for path in (
            html_path, image_path)):
        raise OatlyLinkError('browser evidence escaped the private data root')
    if (not html_path.is_file() or not image_path.is_file()
            or html_path.stat().st_size > MAX_HTML_BYTES
            or image_path.stat().st_size > MAX_HTML_BYTES):
        raise OatlyLinkError('missing/oversized Chromium HTML or screenshot')
    raw = html_path.read_bytes()
    screenshot = image_path.read_bytes()
    html_digest = hashlib.sha256(raw).hexdigest()
    screen_digest = hashlib.sha256(screenshot).hexdigest()
    if (html_digest != info['rendered_html_sha256']
            or screen_digest != info['screenshot_sha256']
            or len(raw) != info.get('rendered_html_bytes')):
        raise OatlyLinkError('Chromium source HTML/screenshot hash mismatch')
    parsed = _VisibleHTML()
    try:
        parsed.feed(raw.decode('utf-8-sig'))
        parsed.close()
    except (UnicodeError, ValueError) as exc:
        raise OatlyLinkError('invalid Chromium source HTML') from exc
    if parsed.title != info['title']:
        raise OatlyLinkError('browser title differs from saved HTML')
    item = analyze_product(
        'meny_oatly_barista_500', raw,
        captured_at=captured_at, retrieval='playwright_render')
    item['browser_screenshot_sha256'] = screen_digest
    item['browser_capture_method'] = info['method']
    item['store_displayed_in_retailer_ui'] = (
        item['store_displayed_in_retailer_ui'] or 'not_identified')
    return item


def join_oatly(pdf_report: Path, source_root: Path, output: Path,
               private_root: Path, *, sources: list[str],
               render_report: Path | None = None) -> dict:
    """Only create a new immutable private join after all evidence checks pass."""
    root = private_root.resolve()
    pdf_report = pdf_report.resolve()
    source_root = source_root.resolve()
    output = output.resolve()
    if (not all(p.is_relative_to(root) for p in
                (pdf_report, source_root, output)) or output == pdf_report):
        raise OatlyLinkError('input, source root and output must stay under data/private')
    if not 1 <= len(sources) <= len(TARGETS) or len(set(sources)) != len(sources):
        raise OatlyLinkError('choose one to four distinct pinned sources')
    if any(s not in TARGET_INDEX for s in sources):
        raise OatlyLinkError('only four selected Oatly first-party pages allowed')
    if output.exists():
        raise OatlyLinkError('private output exists; never overwrite evidence')
    anchor = _load_pdf_anchor(pdf_report)
    if render_report is not None:
        if sources != ['meny_oatly_barista_500']:
            raise OatlyLinkError('only the pinned Barista SKU has browser evidence')
        pages = [_load_render_snapshot(render_report, root)]
    else:
        pages = [_load_snapshot(source, source_root) for source in sources]
    return_val = {
        'source': 'meny_oatly_pdf_and_product_html_source_only_join',
        'pdf_sha256': anchor['pdf_sha256'],
        'pdf_page': 3,
        'pdf_offer_kind': 'ongoing_3_for_2',
        'pdf_base_price_ore': None,
        'campaign_url': CAMPAIGN_URL,
        'promotion_terms_from_pdf': (
            'three_eligible_items; 33_percent_of_sum_deducted_at_checkout'),
        'checkout_discount_math_verified': False,
        'store_price_verified': False,
        'ean_verified': False,
        'branded_protein_verified': False,
        'eligible_online_product_price_candidates': pages,
        'product_capture_provenance': (
            'playwright_render' if render_report is not None else 'public_http'),
        'browser_capture_not_equivalent_to_http_snapshot': (
            render_report is not None),
        'other_pinned_products_not_collected':
            [t.source for t in TARGETS if t.source not in sources],
        'review_state': 'source_only',
        'verified_protein_value_rows': 0,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(return_val, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    return return_val
