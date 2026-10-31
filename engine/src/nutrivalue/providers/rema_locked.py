"""Offline parser and PRIVATE source-only staging for REMA Låst pris cards.

No fetch, product matching, retailer-store claims or verified protein ranking.
Only text visibly scoped inside each <li><article> card is trusted.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

SOURCE_URL = 'https://www.rema.no/kampanjer/last-pris/'
MAX_HTML_BYTES = 5_000_000
PRICE_RE = re.compile(r'([1-9]\d{0,4}|[1-9]\d{0,2}(?: \d{3})+|0),(\d{2})\s*kr', re.IGNORECASE)
DETAIL_RE = re.compile(r'^(.*?)\s*-\s*([1-9]\d{0,4}(?:,\d{2})?|[1-9]\d{0,2}(?: \d{3})+(?:,\d{2})?)\s*kr\.\s*(kg|l|stk|tab)\s*$', re.I)
PACK_RE = re.compile(r'^\s*(?:(\d+)\s*[xX]\s*)?(\d+(?:[,.]\d+)?)\s*(g|kg|ml|l|pk|stk|tab)\s*$', re.I)
VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'))


class RemaLockedError(ValueError):
    """Saved source HTML or a visible card was missing required evidence."""


def _clean(value: str) -> str:
    return ' '.join(value.split())


def _ore(amount: str) -> int:
    match = PRICE_RE.fullmatch(_clean(amount))
    if not match:
        raise RemaLockedError('ambiguous price text')
    result = int(match.group(1).replace(' ', '')) * 100 + int(match.group(2))
    if result <= 0:
        raise RemaLockedError('price must be positive')
    return result


@dataclass(frozen=True)
class LockedCard:
    name: str
    price_ore: int
    details: str
    package_quantity: str | None
    package_unit: str | None
    pack_count: int | None
    unit_price_ore: int | None
    unit_price_unit: str | None
    # No brand, exact EAN, edible yield, campaign dates or local-store proof here.


def _card(name: list[str], prices: list[str], details: list[str]) -> LockedCard:
    if len(name) != 1 or not name[0] or len(details) != 1 or not details[0] or len(prices) != 1:
        raise RemaLockedError('card has missing or ambiguous name, price or details')
    price = _ore(prices[0])
    full_details = details[0]
    match = DETAIL_RE.fullmatch(full_details)
    pack_text = match.group(1) if match else full_details
    unit_ore = None
    unit_kind = None
    if match:
        unit_ore = _ore((match.group(2) if ',' in match.group(2) else match.group(2) + ',00') + ' kr')
        unit_kind = match.group(3).lower()
    pack = PACK_RE.fullmatch(pack_text)
    quantity = None
    unit = None
    count = None
    if pack:
        count = int(pack.group(1)) if pack.group(1) else None
        quantity = str(Decimal(pack.group(2).replace(',', '.')))
        unit = pack.group(3).lower()
        if Decimal(quantity) <= 0 or (count is not None and count <= 0):
            raise RemaLockedError('non-positive package quantity')
    return LockedCard(name[0], price, full_details, quantity, unit, count, unit_ore, unit_kind)


class _CardReader(HTMLParser):
    """Capture product fields only in one article, never in adjacent cards."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.li_depth = 0
        self.article_depth = 0
        self.capture: tuple[str, int] | None = None
        self.parts: list[str] = []
        self.name: list[str] = []
        self.details: list[str] = []
        self.prices: list[str] = []
        self.cards: list[LockedCard] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        if tag == 'li':
            self.li_depth += 1
        if tag == 'article' and self.li_depth and not self.article_depth:
            self.article_depth = 1
            self.name, self.details, self.prices = [], [], []
            return
        if not self.article_depth:
            return
        if tag not in VOID:
            self.article_depth += 1
        if self.capture is None:
            kind = ('name' if tag == 'h3' else
                    'details' if tag == 'p' and 'text-body-xs' in classes and
                    'text-text-secondary' in classes else
                    'price' if tag == 'span' and 'sr-only' in classes else None)
            if kind is not None:
                self.capture = (kind, self.article_depth)
                self.parts = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.article_depth and self.capture is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if self.article_depth:
            if self.capture and self.capture[1] == self.article_depth:
                text = _clean(''.join(self.parts))
                kind = self.capture[0]
                if kind == 'name':
                    self.name.append(text)
                elif kind == 'details':
                    self.details.append(text)
                elif PRICE_RE.fullmatch(text):
                    # Ignore the 'Før' label: never take the struck-out old price.
                    self.prices.append(text)
                self.capture = None
                self.parts = []
            if tag == 'article' and self.article_depth == 1:
                # A sourced card without all three expected elements is RED.
                self.cards.append(_card(self.name, self.prices, self.details))
                self.article_depth = 0
            elif tag not in VOID:
                self.article_depth -= 1
        if tag == 'li':
            self.li_depth -= 1


def parse_html(html: str) -> tuple[LockedCard, ...]:
    if 'Låst pris' not in html or 'Kampanjeprodukter' not in html:
        raise RemaLockedError('not a recognizable REMA Låst pris page')
    reader = _CardReader()
    reader.feed(html)
    reader.close()
    if reader.article_depth or not reader.cards:
        raise RemaLockedError('no complete source cards')
    return tuple(reader.cards)


def stage_html(payload: bytes, report: dict, db_path: Path, schema_path: Path) -> tuple[int, str]:
    if not payload or len(payload) > MAX_HTML_BYTES:
        raise RemaLockedError('missing or oversized saved HTML')
    digest = hashlib.sha256(payload).hexdigest()
    if (report.get('source') != 'rema_locked' or report.get('source_url') != SOURCE_URL
            or report.get('final_url') != SOURCE_URL or report.get('sha256') != digest
            or report.get('html_bytes') != len(payload)):
        raise RemaLockedError('source report does not match saved HTML')
    fetched_at = report.get('fetched_at')
    try:
        fetch_date = datetime.fromisoformat(fetched_at)
        if fetch_date.tzinfo is None or fetch_date.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise RemaLockedError('source report lacks timezone-aware fetched_at') from exc
    try:
        source = payload.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise RemaLockedError('HTML is not UTF-8') from exc
    cards = parse_html(source)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(schema_path.read_text(encoding='utf-8'))
        db.execute('BEGIN IMMEDIATE')
        db.execute('''INSERT OR IGNORE INTO rema_locked_snapshots
                    (sha256, source_url, fetched_at, original_html)
                    VALUES (?, ?, ?, ?)''', (digest, SOURCE_URL, fetched_at, source))
        db.executemany('''INSERT OR IGNORE INTO rema_locked_cards
            (snapshot_sha256, card_no, product_name, price_ore, price_basis,
             details_text, package_quantity, package_unit, pack_count,
             unit_price_ore, unit_price_unit, review_state)
            VALUES (?, ?, ?, ?, 'per_package', ?, ?, ?, ?, ?, ?, 'source_only')''',
            [(digest, n, c.name, c.price_ore, c.details, c.package_quantity,
              c.package_unit, c.pack_count, c.unit_price_ore, c.unit_price_unit)
             for n, c in enumerate(cards, start=1)])
    return len(cards), digest
