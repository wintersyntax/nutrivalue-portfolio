"""Strict parser for KIWI's publicly displayed weekly price-check TABLE.

A weekly price check is NOT a complete weekly flyer or a verified local shelf
price. These are provider-reported candidates only; no automatic nutrition
matching or trusted price publication happens here.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

SOURCE_URL = "https://kiwi.no/dagligvarer/prissjekk"
MAX_HTML_BYTES = 2_000_000
PROTEIN_TERMS = (
    "KYLLING", "KJØTT", "TORSK", "LAKS", "ØRRET", "FISK", "EGG",
    "SKYR", "KESAM", "YOGHURT", "KEFIR", "OST", "SALAMI", "BURGER",
    "LEVERPOSTEI", "BACON", "BØNN", "LINSE",
)
WEEK_RE = re.compile(r"\bUke\s+(\d{1,2})\b", re.IGNORECASE)
UNTIL_RE = re.compile(r"Gjelder\s+til\s+og\s+med\s+(\d{1,2}\.\d{1,2}\.\d{4})", re.IGNORECASE)
PRICE_RE = re.compile(r"(\d+(?:[,.]\d{1,2})?)(?:\s*pr\.?\s*kg)?", re.IGNORECASE)


class PriceCheckError(ValueError):
    """Source structure changed or a field cannot be validated."""


def cleaned(text: str) -> str:
    return " ".join(text.split())


class _TableReader(HTMLParser):
    """Read semantically marked HTML tables; never interpret page scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.all_text: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.depth = 0
        self.skip = 0
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "table":
            if self.depth == 0:
                self.rows = []
            self.depth += 1
        elif self.depth and tag == "tr":
            self.row = []
        elif self.depth and tag in ("td", "th"):
            self.cell = []

    def handle_data(self, data):
        if self.skip:
            return
        if data.strip():
            self.all_text.append(cleaned(data))
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append(cleaned(" ".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.depth == 1:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.tables.append(self.rows)
                self.rows = []


def _cents(value: str, *, allow_empty=False) -> tuple[int | None, str]:
    raw = cleaned(value)
    if not raw and allow_empty:
        return None, "unknown"
    match = PRICE_RE.fullmatch(raw)
    if match is None:
        raise PriceCheckError(f"unsupported price format: {raw[:45]!r}")
    number = match.group(1).replace(",", ".")
    try:
        cents = int(Decimal(number) * 100)
    except (ValueError, InvalidOperation) as exc:
        raise PriceCheckError("invalid price") from exc
    if cents <= 0:
        raise PriceCheckError("price must be positive")
    basis = "per_kg" if re.search(r"pr\.?\s*kg", raw, re.I) else "per_package"
    return cents, basis


@dataclass(frozen=True)
class PriceRow:
    name: str
    old_cents: int | None
    new_cents: int
    low_30d_cents: int | None
    basis: str
    candidate_protein: bool


@dataclass(frozen=True)
class PriceCheck:
    week: int
    year: int
    valid_through: str
    rows: tuple[PriceRow, ...]


def parse_html(source: str) -> PriceCheck:
    reader = _TableReader()
    reader.feed(source)
    reader.close()
    # Parse only visible text. A price in embedded JSON is not proof of
    # its presentation as an offer to the user.
    text = " ".join(reader.all_text)
    week = WEEK_RE.search(text)
    until = UNTIL_RE.search(text)
    if not week or not until:
        raise PriceCheckError("source week or explicit valid-through date missing")
    try:
        end = datetime.strptime(until.group(1), "%d.%m.%Y").date()
    except ValueError as exc:
        raise PriceCheckError("invalid source validity date") from exc
    week_number = int(week.group(1))
    year, actual_week, _ = end.isocalendar()
    if actual_week != week_number:
        raise PriceCheckError("week heading and source expiry do not match")

    candidates: list[tuple[list[list[str]], int]] = []
    for table in reader.tables:
        for i, row in enumerate(table):
            headings = [cell.upper() for cell in row]
            if "VARETEKST" in headings and "NY UTPRIS" in headings and len(row) == 4:
                candidates.append((table, i))
    if len(candidates) != 1:
        raise PriceCheckError("expected exactly one KIWI price-check table")

    table, header_at = candidates[0]
    parsed: list[PriceRow] = []
    for cells in table[header_at + 1:]:
        if not any(cells):
            continue
        if len(cells) == 3:
            # A live KIWI row has no final 30-day-low cell at all.
            # Its first three cells still mean name / old / new.
            name, old, new = cells
            low = ""
        elif len(cells) == 4:
            name, old, new, low = cells
        else:
            raise PriceCheckError("source price row has unexpected columns")
        if not name or not new:
            raise PriceCheckError("source price row missing product name or new price")
        amount, basis = _cents(new)
        # KIWI sometimes lists approximate-weight products (e.g. "CA300G")
        # at a large price without a unit. Do not silently label those as
        # package prices: the source does not prove NOK/package vs NOK/kg.
        if basis == "per_package" and re.search(
            r"\bCA\s*\d+(?:[,.]\d+)?\s*(?:KG|G)\b", name, re.IGNORECASE
        ):
            basis = "unknown"
        old_amount, old_basis = _cents(old, allow_empty=True)
        low_amount, low_basis = _cents(low, allow_empty=True)
        if old_basis == "per_kg" or low_basis == "per_kg":
            raise PriceCheckError("unexpected old/low price basis")
        parsed.append(PriceRow(
            name=name,
            old_cents=old_amount,
            new_cents=amount,
            low_30d_cents=low_amount,
            basis=basis,
            candidate_protein=any(term in name.upper() for term in PROTEIN_TERMS),
        ))
    if not parsed:
        raise PriceCheckError("no priced rows found; source structure may have changed")
    return PriceCheck(week_number, year, end.isoformat(), tuple(parsed))


def _upgrade_legacy_price_basis(db: sqlite3.Connection) -> None:
    """Preserve existing source candidates when adding the 'unknown' unit.

    The initial private pilot schema admitted only 'per_package'/'per_kg'.
    Keep its rows, original snapshot IDs and indexes during this one-time
    migration, including when source HTML has been fetched previously.
    """
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='weekly_pricecheck_candidates'"
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise PriceCheckError("weekly price-check table is missing")
    old_basis = "('per_package', 'per_kg')"
    new_basis = "('per_package', 'per_kg', 'unknown')"
    if new_basis in row[0]:
        return
    if row[0].count(old_basis) != 1:
        raise PriceCheckError("unsupported existing weekly candidate table")
    ddl = row[0].replace(old_basis, new_basis)
    db.execute(
        "ALTER TABLE weekly_pricecheck_candidates "
        "RENAME TO weekly_pricecheck_candidates_legacy"
    )
    db.execute(ddl)
    db.execute(
        "INSERT INTO weekly_pricecheck_candidates SELECT * "
        "FROM weekly_pricecheck_candidates_legacy"
    )
    db.execute("DROP TABLE weekly_pricecheck_candidates_legacy")
    db.execute(
        "CREATE INDEX weekly_pricecheck_review_idx "
        "ON weekly_pricecheck_candidates(candidate_protein, price_basis)"
    )
    # Previously stored candidates still have 'per_package' from the
    # first parser version. Correct only rows whose product text explicitly
    # says approximate mass but source price has no stated unit.
    candidates = db.execute(
        "SELECT snapshot_sha256, row_no, product_text "
        "FROM weekly_pricecheck_candidates WHERE price_basis='per_package'"
    ).fetchall()
    for digest, row_no, product_name in candidates:
        if re.search(
            r"\bCA\s*\d+(?:[,.]\d+)?\s*(?:KG|G)\b",
            product_name, re.IGNORECASE,
        ):
            db.execute(
                "UPDATE weekly_pricecheck_candidates SET price_basis='unknown' "
                "WHERE snapshot_sha256=? AND row_no=?",
                (digest, row_no),
            )


def stage_html(html: bytes, db_path: Path, schema_path: Path, *, fetched_at: str | None = None):
    if not html or len(html) > MAX_HTML_BYTES:
        raise PriceCheckError("HTML is empty or too large")
    try:
        source = html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PriceCheckError("invalid UTF-8 HTML") from exc
    parsed = parse_html(source)  # Validate all records BEFORE mutating SQLite.
    stamp = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    digest = hashlib.sha256(html).hexdigest()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(schema_path.read_text(encoding="utf-8"))
        # SQLite executescript commits implicitly. All migration and inserted
        # candidates must be atomic after the schema was initialized.
        db.execute("BEGIN IMMEDIATE")
        _upgrade_legacy_price_basis(db)
        db.execute(
            """INSERT OR IGNORE INTO weekly_pricecheck_snapshots
               (sha256, source_url, fetched_at, iso_year, iso_week, valid_through, original_html)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (digest, SOURCE_URL, stamp, parsed.year, parsed.week, parsed.valid_through, source),
        )
        db.executemany(
            """INSERT OR IGNORE INTO weekly_pricecheck_candidates
               (snapshot_sha256, row_no, chain_name, product_text, old_cents,
                price_cents, low_30d_cents, price_basis, candidate_protein, review_state)
               VALUES (?, ?, 'KIWI', ?, ?, ?, ?, ?, ?, 'source_only')""",
            [(digest, index, row.name, row.old_cents, row.new_cents,
              row.low_30d_cents, row.basis, int(row.candidate_protein))
             for index, row in enumerate(parsed.rows, 1)],
        )
    return parsed, digest
