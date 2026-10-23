"""Offline KIWI Prislås card parser and private, source-only SQLite staging.

Only visible fields *inside the same HTML campaign card* are associated.
No HTTP requests, product/EAN/nutrition matching, validity inference or ranking.
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

SOURCE_URL = "https://kiwi.no/dagligvarer/prislaas"
MAX_HTML_BYTES = 2_000_000
VOID = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input",
                  "link", "meta", "param", "source", "track", "wbr"))
UNIT_RE = re.compile(
    r"^pr\.\s*(100\s*m|kg|liter|l|stk|m|pk)\.?\s*(\d+(?:,\d{1,2})?)?$",
    flags=re.IGNORECASE,
)
PACK_RE = re.compile(
    r"^(?:(\d+)\s*[xX]\s*)?(\d+(?:[,.]\d+)?)\s*"
    r"(g|kg|ml|liter|l|stk|cm|m|ark|rull|pk)\.?$",
    flags=re.IGNORECASE,
)
HYPHEN_PACK_RE = re.compile(r"^(\d+)\s*-\s*pk\.?$", flags=re.IGNORECASE)
CATEGORIES = (
    ("campaignCard_productName__", "name"),
    ("campaignCard_description__", "description"),
    ("campaignCard_campaignDescription__", "unit_description"),
    ("campaignCard_mainPrice__", "whole"),
    ("campaignCard_decimals__", "fraction"),
    ("campaignCard_recyclable__", "deposit"),
)


class KiwiLockedError(ValueError):
    """Malformed, ambiguous or mismatched saved KIWI evidence."""


def _clean(value: str) -> str:
    return " ".join(value.split())


def _ore(raw: str, *, allow_integer: bool = False) -> int:
    raw = _clean(raw)
    pattern = r"\d{1,6}(?:,\d{1,2})?" if allow_integer else r"\d{1,6},\d{2}"
    if not re.fullmatch(pattern, raw):
        raise KiwiLockedError("invalid visible monetary field")
    result = int((Decimal(raw.replace(",", ".")) * 100).to_integral_exact())
    if result <= 0:
        raise KiwiLockedError("non-positive monetary field")
    return result


@dataclass(frozen=True)
class LockedCard:
    name: str
    description: str
    price_ore: int
    price_basis: str
    deposit_applies: bool
    package_quantity: str | None
    package_unit: str | None
    pack_count: int | None
    source_unit_text: str
    source_unit_price_ore: int | None
    source_unit_price_unit: str | None
    unit_price_check: str  # match, mismatch, unchecked


def _package(description: str) -> tuple[str | None, str | None, int | None]:
    first = description.split(", ", 1)[0]
    # Decimal commas are not delimiters: "1,75 liter, Tine" is one quantity.
    match = PACK_RE.fullmatch(first)
    if match:
        count = int(match.group(1)) if match.group(1) else None
        quantity = Decimal(match.group(2).replace(",", "."))
        if quantity <= 0 or (count is not None and count <= 0):
            raise KiwiLockedError("non-positive package quantity")
        unit = match.group(3).lower()
        return str(quantity), ("liter" if unit == "l" else unit), count
    pack = HYPHEN_PACK_RE.fullmatch(first)
    if pack:
        quantity = int(pack.group(1))
        if quantity <= 0:
            raise KiwiLockedError("non-positive number in package")
        return str(quantity), "pk", None
    return None, None, None


def _source_unit(text: str) -> tuple[int | None, str | None]:
    if not text:
        return None, None
    found = UNIT_RE.fullmatch(text.strip())
    if not found:
        # Preserve unfamiliar published text, never invent a parsed unit rate.
        return None, None
    kind = found.group(1).replace(" ", "").lower()
    if kind == "l":
        kind = "liter"
    amount = found.group(2)
    return (_ore(amount, allow_integer=True) if amount else None), kind


def _unit_check(price_ore: int, package_quantity: str | None, package_unit: str | None,
                pack_count: int | None, source_unit_ore: int | None,
                source_unit_unit: str | None) -> str:
    if not package_quantity or not package_unit or not source_unit_ore or not source_unit_unit:
        return "unchecked"
    numerator = Decimal(package_quantity) * (pack_count or 1)
    factors = {
        ("g", "kg"): Decimal("0.001"),
        ("kg", "kg"): Decimal(1),
        ("ml", "liter"): Decimal("0.001"),
        ("liter", "liter"): Decimal(1),
        ("stk", "stk"): Decimal(1),
        ("m", "m"): Decimal(1),
        ("cm", "m"): Decimal("0.01"),
        ("m", "100m"): Decimal("0.01"),
        ("cm", "100m"): Decimal("0.0001"),
    }
    conversion = factors.get((package_unit, source_unit_unit))
    if conversion is None:
        return "unchecked"
    base = numerator * conversion
    if base <= 0:
        return "unchecked"
    expected = Decimal(price_ore) / base
    # Published rates generally round to øre; <=1 øre tolerates half-way ties.
    return "match" if abs(expected - source_unit_ore) <= 1 else "mismatch"


def _from_fields(fields: dict[str, list[str]]) -> LockedCard:
    required = ("name", "description", "whole", "fraction")
    if any(len(fields[k]) != 1 or not fields[k][0] for k in required):
        raise KiwiLockedError("missing/duplicate product, description or split price in a card")
    if len(fields["unit_description"]) != 1:
        raise KiwiLockedError("missing/duplicate source unit description")
    if len(fields["deposit"]) > 1 or any(d.casefold() != "+ pant" for d in fields["deposit"]):
        raise KiwiLockedError("ambiguous deposit marker")
    whole, decimals = fields["whole"][0], fields["fraction"][0]
    if not re.fullmatch(r"\d{1,6}", whole) or not re.fullmatch(r"\d{2}", decimals):
        raise KiwiLockedError("price is not a two-part integer-øre amount")
    ore = int(whole) * 100 + int(decimals)
    if ore <= 0:
        raise KiwiLockedError("price must be positive")
    quantity, unit, count = _package(fields["description"][0])
    unit_text = fields["unit_description"][0]
    unit_ore, unit_kind = _source_unit(unit_text)
    check = _unit_check(ore, quantity, unit, count, unit_ore, unit_kind)
    return LockedCard(fields["name"][0], fields["description"][0], ore,
                      "per_package", bool(fields["deposit"]), quantity, unit,
                      count, unit_text, unit_ore, unit_kind, check)


class _CardReader(HTMLParser):
    """Capture fields within a single campaign article, never a global text match."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.fields: dict[str, list[str]] = {}
        self.active: list[tuple[str, int, list[str]]] = []
        self.cards: list[LockedCard] = []
        self.h1: list[str] = []
        self.h1_active: list[str] | None = None
        self.page_kiwi = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "html" and attrs.get("data-chain") == "kiwi":
            self.page_kiwi = True
        if tag in ("script", "style", "template"):
            self._skip += 1
        if not self.depth:
            if tag == "h1":
                self.h1_active = []
            if tag == "article" and any(c.startswith("campaignCard_card__") for c in classes):
                self.depth = 1
                self.fields = {kind: [] for _, kind in CATEGORIES}
                self.active = []
            return
        if tag not in VOID:
            self.depth += 1
        for prefix, kind in CATEGORIES:
            if any(c.startswith(prefix) for c in classes):
                self.active.append((kind, self.depth, []))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self._skip:
            return
        if self.h1_active is not None:
            self.h1_active.append(data)
        for _, _, parts in self.active:
            parts.append(data)

    def handle_endtag(self, tag):
        if tag == "h1" and self.h1_active is not None:
            self.h1.append(_clean(" ".join(self.h1_active)))
            self.h1_active = None
        if self.depth:
            to_close = [cap for cap in self.active if cap[1] == self.depth]
            for kind, _, parts in to_close:
                self.fields[kind].append(_clean(" ".join(parts)))
                self.active.remove((kind, self.depth, parts))
            if tag == "article" and self.depth == 1:
                self.cards.append(_from_fields(self.fields))
                self.depth = 0
                self.fields = {}
                self.active = []
            elif tag not in VOID:
                self.depth -= 1
        if tag in ("script", "style", "template") and self._skip:
            self._skip -= 1


def parse_html(html: str) -> tuple[LockedCard, ...]:
    reader = _CardReader()
    reader.feed(html)
    reader.close()
    if not reader.page_kiwi or not any("Prislås hos KIWI" in name for name in reader.h1):
        raise KiwiLockedError("not a recognizable KIWI Prislås page")
    if reader.depth or not reader.cards:
        raise KiwiLockedError("missing or unclosed KIWI campaign cards")
    return tuple(reader.cards)


def stage_html(payload: bytes, report: dict, db_path: Path, schema_path: Path) -> tuple[int, str]:
    if not isinstance(report, dict):
        raise KiwiLockedError("source report must be an object")
    if not payload or len(payload) > MAX_HTML_BYTES:
        raise KiwiLockedError("missing or oversized KIWI HTML")
    digest = hashlib.sha256(payload).hexdigest()
    if (report.get("source") != "kiwi_locked" or report.get("source_url") != SOURCE_URL
            or report.get("final_url") != SOURCE_URL or report.get("sha256") != digest
            or report.get("html_bytes") != len(payload)):
        raise KiwiLockedError("source report does not match saved HTML")
    try:
        stamp = datetime.fromisoformat(report.get("fetched_at"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise KiwiLockedError("source report lacks timezone-aware fetch date") from exc
    try:
        html = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KiwiLockedError("HTML not UTF-8") from exc
    cards = parse_html(html)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(schema_path.read_text(encoding="utf-8"))
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT OR IGNORE INTO kiwi_locked_snapshots
                      (sha256, source_url, fetched_at, original_html)
                      VALUES (?, ?, ?, ?)""", (digest, SOURCE_URL, report["fetched_at"], html))
        db.executemany("""INSERT OR IGNORE INTO kiwi_locked_cards
             (snapshot_sha256, card_no, product_name, details_text, price_ore,
              price_basis, deposit_applies, package_quantity, package_unit,
              pack_count, source_unit_text, unit_price_ore, unit_price_unit,
              unit_price_check, review_state)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'source_only')""",
             [(digest, i, c.name, c.description, c.price_ore, c.price_basis,
               int(c.deposit_applies), c.package_quantity, c.package_unit,
               c.pack_count, c.source_unit_text, c.source_unit_price_ore,
               c.source_unit_price_unit, c.unit_price_check)
              for i, c in enumerate(cards, 1)])
    return len(cards), digest
