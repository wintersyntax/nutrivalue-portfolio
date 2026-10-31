"""TASK-023: offline-only Tjek snapshot parser and PRIVATE source-only staging.

Accepts a supplied JSON envelope or kundeavis-mcp v1 .ndjson.gz archive.
NO HTTP client, automatic source refresh, SKU joining or price verification.
Inspired by the public MIT-licensed donadelicc/kundeavis-mcp Tjek shapes;
this Python implementation does not copy its source.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

MAX_FILE_BYTES = 12_000_000
MAX_DECODED_BYTES = 35_000_000
MAX_ENTRIES = 100
MAX_OFFERS = 5_000

# Owner-selected chain preference, not proof of an individual nearby store.
# These dealer IDs were reported by donadelicc/kundeavis-mcp, not all live-tested.
CHAINS = {
    "5b11sm": "Bunnpris",
    "de79dm": "Coop Mega",
    "f5d5lm": "Coop Prix",
    "80742m": "Extra",
    "b3e8Fm": "Joker",
    "257bxm": "KIWI",
    "2686gD": "Matkroken",
    "4333pm": "MENY",
    "faa0Ym": "REMA 1000",
}
UNIT_FACTORS = {
    "g": ("kg", Decimal("0.001")),
    "hg": ("kg", Decimal("0.1")),
    "kg": ("kg", Decimal("1")),
    "ml": ("l", Decimal("0.001")),
    "cl": ("l", Decimal("0.01")),
    "dl": ("l", Decimal("0.1")),
    "l": ("l", Decimal("1")),
}
NUM = r"\d+(?:,\d{1,2})?"
AFTER = re.compile(r"\b(?:pr|per)\.?\s*(kg|l)\b[\s:]*(" + NUM + r")", re.I)
BEFORE = re.compile(r"(" + NUM + r")\s*(?:pr|per)\.?\s*(kg|l)\b", re.I)
SUFFIX = re.compile(r"(" + NUM + r")\s*/\s*(kg|l)\b", re.I)
MEMBER = re.compile(r"medlem|trumf|kundeklubb", re.I)
# A multi-buy banner may say "2 for" with a price in another field.
# Withhold a derived unit rate even when the numeric multi-buy total is absent.
MULTIBUY = re.compile(r"\b\d+\s*(?:stk\s*)?for\b", re.I)
DEPOSIT = re.compile(r"\bpant\b", re.I)


class TjekSnapshotError(ValueError):
    """Malformed/unattributable saved source data: never silently invent fields."""


@dataclass(frozen=True)
class Snapshot:
    sha256: str
    fetched_at: str
    format: str
    raw_payload: bytes
    catalogs: tuple[tuple, ...]
    offers: tuple[tuple, ...]


def _object(value, name):
    if not isinstance(value, dict):
        raise TjekSnapshotError(f"{name} must be an object")
    return value


def _list(value, name):
    if not isinstance(value, list):
        raise TjekSnapshotError(f"{name} must be a list")
    return value


def _text(value, name, *, optional=False, max_length=4096):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise TjekSnapshotError(f"{name} must be nonempty bounded text")
    return value.strip()


def _time(value, name, *, optional=False):
    if optional and value is None:
        return None
    raw = _text(value, name, max_length=64)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
    except ValueError as exc:
        raise TjekSnapshotError(f"{name} needs timezone-aware ISO timestamp") from exc
    return raw


FLOAT_NOISE_ORE = Decimal("0.000001")


def _money(value, name, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise TjekSnapshotError(f"{name} must be a numeric NOK amount")
    try:
        dec = Decimal(str(value))
        ore = dec * 100
        if isinstance(value, float) and dec.is_finite():
            # TASK-078: JSON floats such as 41.900000000000006 are binary noise
            # around a two-decimal price (W44 KIWI); the raw file keeps the
            # original. A real third decimal (41.905) is still rejected.
            nearest = ore.to_integral_value()
            if abs(ore - nearest) <= FLOAT_NOISE_ORE:
                ore = nearest
        if not dec.is_finite() or dec <= 0 or ore != ore.to_integral_value():
            raise ValueError
        if ore > 100_000_000:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise TjekSnapshotError(f"{name} must be positive NOK with max two decimals") from None
    return int(ore)


def _count(value, name, *, optional=True):
    if value is None and optional:
        return None
    if type(value) is not int or value < 0 or value > MAX_OFFERS:
        raise TjekSnapshotError(f"{name} must be a bounded integer")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number <= 0 or number > 1_000_000:
        return None
    return number


def _quantity(value):
    """Quantity is per-item size times pieces, not one headline unit.

    Unknown/ranged/mismatched SI fields withhold computed unit rates.
    """
    if value is None:
        return None, None, None, "missing_quantity", None, None
    q = _object(value, "offer.quantity")
    unit_obj = q.get("unit")
    if not isinstance(unit_obj, dict):
        return None, None, None, "missing_unit", None, None
    unit = unit_obj.get("symbol")
    if not isinstance(unit, str) or not unit.strip():
        return None, None, None, "missing_unit", None, None
    unit = unit.lower().strip()
    size = q.get("size")
    pieces = q.get("pieces")
    if not isinstance(size, dict):
        return None, unit, None, "missing_size", None, None
    low, high = _number(size.get("from")), _number(size.get("to"))
    if low is None or (high is not None and low != high):
        return None, unit, None, "ambiguous_size", None, None
    if pieces is None:
        count = 1
    elif isinstance(pieces, dict):
        a, b = _number(pieces.get("from")), _number(pieces.get("to"))
        if a is None or a != a.to_integral_value() or (b is not None and b != a):
            return str(low), unit, None, "ambiguous_pieces", None, None
        count = int(a)
        if count > 1000:
            return str(low), unit, None, "ambiguous_pieces", None, None
    else:
        return str(low), unit, None, "ambiguous_pieces", None, None
    if unit not in UNIT_FACTORS:
        return str(low), unit, count, "unsupported_unit", None, None
    basis, factor = UNIT_FACTORS[unit]
    si = unit_obj.get("si")
    if isinstance(si, dict) and si.get("symbol") is not None:
        si_symbol = si.get("symbol")
        si_factor = _number(si.get("factor"))
        if si_symbol != basis or si_factor != factor:
            return str(low), unit, count, "si_unit_conflict", None, None
    total = low * count * factor
    if total <= 0:
        return str(low), unit, count, "ambiguous_quantity", None, None
    return str(low), unit, count, "exact_quantity", basis, total


def _printed_rates(description):
    if not description:
        return set()
    candidates = set()
    for regex in (AFTER, BEFORE, SUFFIX):
        for match in regex.finditer(description):
            number, basis = (match.group(2), match.group(1)) if regex is AFTER else match.groups()
            # A numeric pack multiplier or a former price is not today's unit rate.
            tail = description[match.end():match.end()+12]
            previous = description[max(0, match.start()-16):match.start()]
            if re.match(r"\s*[x×]\s", tail, re.I) or re.search(r"før(?:pris)?\s*$", previous, re.I):
                continue
            try:
                rate = _money(number.replace(",", "."), "printed unit price")
            except TjekSnapshotError:
                continue
            candidates.add((basis.lower(), rate))
    return candidates


def _unit_price(price_ore, quantity, description, member, multibuy, deposit):
    size, unit, count, diagnostic, basis, total = _quantity(quantity)
    printed = _printed_rates(description)
    printed_ore = next(iter(printed))[1] if len(printed) == 1 else None
    if price_ore is None or basis is None or total is None:
        state = "missing_or_ambiguous_price" if price_ore is None else diagnostic
        return size, unit, count, diagnostic, None, None, printed_ore, state
    computed = int((Decimal(price_ore) / total).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if computed <= 0:
        return size, unit, count, diagnostic, None, None, printed_ore, "rate_below_one_ore"
    if deposit:
        return size, unit, count, diagnostic, None, computed, printed_ore, "withheld_deposit"
    if multibuy:
        return size, unit, count, diagnostic, None, computed, printed_ore, "withheld_multibuy"
    if member:
        return size, unit, count, diagnostic, None, computed, printed_ore, "withheld_membership"
    if printed:
        same = {rate for pr_basis, rate in printed if pr_basis == basis}
        if len(same) != 1 or len(printed) != 1:
            return size, unit, count, diagnostic, None, computed, printed_ore, "ambiguous_printed_rate"
        source = next(iter(same))
        # Tjek printed-unit-price rounding can vary slightly, but unexplained
        # disagreement is never elevated to a usable unit rate.
        tolerance = max(2, int((Decimal(computed) * Decimal("0.005")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
        if abs(computed - source) > tolerance:
            return size, unit, count, diagnostic, None, computed, source, "source_rate_mismatch"
        return size, unit, count, diagnostic, computed, computed, source, "source_print_consistent"
    return size, unit, count, diagnostic, computed, computed, None, "calculated_unchecked"


def _decode(raw, filename):
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise TjekSnapshotError("missing or oversized source snapshot")
    fmt = "kundeavis_ndjson_gz" if filename.endswith(".ndjson.gz") else "json" if filename.endswith(".json") else None
    if fmt is None:
        raise TjekSnapshotError("only .json and kundeavis .ndjson.gz accepted")
    if fmt == "kundeavis_ndjson_gz":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                decoded = gz.read(MAX_DECODED_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise TjekSnapshotError("invalid gzip snapshot") from exc
    else:
        decoded = raw
    if len(decoded) > MAX_DECODED_BYTES:
        raise TjekSnapshotError("decompressed snapshot exceeds limit")
    try:
        data = decoded.decode("utf-8")
    except UnicodeError as exc:
        raise TjekSnapshotError("source must be UTF-8") from exc
    try:
        if fmt == "json":
            envelope = _object(json.loads(data), "snapshot")
            fetched = _time(envelope.get("fetched_at"), "fetched_at")
            entries = envelope.get("entries")
            if entries is None:
                entries = [{"catalog": envelope.get("catalog"), "offers": envelope.get("offers")}]
            entries = _list(entries, "entries")
        else:
            lines = data.splitlines()
            if not lines:
                raise TjekSnapshotError("empty NDJSON snapshot")
            header = _object(json.loads(lines[0]), "header")
            if header.get("kind") != "header" or header.get("v") != 1:
                raise TjekSnapshotError("unsupported kundeavis snapshot header")
            fetched = _time(header.get("synced_at"), "synced_at")
            entries = []
            index = {}
            count = 0
            for line in lines[1:]:
                record = _object(json.loads(line), "NDJSON entry")
                if record.get("kind") == "catalog":
                    catalog = _object(record.get("catalog"), "catalog")
                    catalog_id = _text(catalog.get("id"), "catalog.id")
                    if catalog_id in index:
                        raise TjekSnapshotError("duplicate catalog in archive")
                    index[catalog_id] = {"catalog": catalog, "offers": []}
                    entries.append(index[catalog_id])
                elif record.get("kind") == "offer":
                    catalog_id = _text(record.get("catalog_id"), "offer.catalog_id")
                    if catalog_id not in index:
                        raise TjekSnapshotError("orphan NDJSON offer")
                    index[catalog_id]["offers"].append(_object(record.get("offer"), "offer"))
                    count += 1
                    if count > MAX_OFFERS:
                        raise TjekSnapshotError("too many offers")
                else:
                    raise TjekSnapshotError("unknown NDJSON record kind")
            if header.get("catalogs") != len(entries) or header.get("offers") != count:
                raise TjekSnapshotError("archive header counts mismatch")
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise TjekSnapshotError("snapshot is not valid UTF-8 JSON") from exc
    if not entries or len(entries) > MAX_ENTRIES:
        raise TjekSnapshotError("empty or excessive catalogue list")
    return fmt, fetched, entries


def parse_snapshot(raw: bytes, filename: str) -> Snapshot:
    fmt, fetched, entries = _decode(raw, filename)
    digest = hashlib.sha256(raw).hexdigest()
    catalogs = []
    offers = []
    seen_catalogs = set()
    for entry in entries:
        entry = _object(entry, "entry")
        cat = _object(entry.get("catalog"), "catalog")
        catalog_id = _text(cat.get("id"), "catalog.id")
        dealer = _text(cat.get("dealer_id"), "catalog.dealer_id")
        if dealer not in CHAINS:
            raise TjekSnapshotError("catalog dealer outside selected nine chains")
        if catalog_id in seen_catalogs:
            raise TjekSnapshotError("duplicate catalog id")
        seen_catalogs.add(catalog_id)
        frm = _time(cat.get("run_from"), "catalog.run_from")
        till = _time(cat.get("run_till"), "catalog.run_till")
        if datetime.fromisoformat(frm.replace("Z", "+00:00")) >= datetime.fromisoformat(till.replace("Z", "+00:00")):
            raise TjekSnapshotError("catalog validity must increase")
        raw_label = cat.get("label")
        # Several valid Coop regional flyers have label=""; the label is
        # optional, unlike catalogue identity, validity and dealer.
        label = (None if isinstance(raw_label, str) and not raw_label.strip()
                 else _text(raw_label, "catalog.label", optional=True))
        published = _time(cat.get("publish"), "catalog.publish", optional=True)
        declared = _count(cat.get("offer_count"), "catalog.offer_count")
        rows = _list(entry.get("offers"), "offers")
        catalogs.append((digest, catalog_id, dealer, CHAINS[dealer], label, frm, till, published, declared))
        seen_offers = set()
        for obj in rows:
            obj = _object(obj, "offer")
            oid = _text(obj.get("id"), "offer.id")
            if oid in seen_offers:
                raise TjekSnapshotError("duplicate offer id within catalog")
            seen_offers.add(oid)
            actual_dealer = _text(obj.get("dealer_id"), "offer.dealer_id")
            if actual_dealer != dealer:
                raise TjekSnapshotError("offer dealer does not match catalog")
            linked_cat = obj.get("catalog_id")
            if linked_cat is not None and linked_cat != catalog_id:
                raise TjekSnapshotError("offer catalog id mismatch")
            offer_frm = _time(obj.get("run_from"), "offer.run_from")
            offer_till = _time(obj.get("run_till"), "offer.run_till")
            if datetime.fromisoformat(offer_frm.replace("Z", "+00:00")) >= datetime.fromisoformat(offer_till.replace("Z", "+00:00")):
                raise TjekSnapshotError("offer validity must increase")
            name = _text(obj.get("heading"), "offer.heading")
            # Provider may serialize an absent optional description as "" or
            # whitespace. The immutable raw source retains the original
            # value; normalized review data uses NULL, never an invented note.
            raw_desc = obj.get("description")
            desc = (None if isinstance(raw_desc, str) and not raw_desc.strip()
                    else _text(raw_desc, "offer.description", optional=True,
                               max_length=8000))
            pricing = _object(obj.get("pricing"), "pricing")
            currency = pricing.get("currency")
            if currency != "NOK":
                raise TjekSnapshotError("only NOK prices accepted")
            price = _money(pricing.get("price"), "pricing.price", optional=True)
            prior = _money(pricing.get("pre_price"), "pricing.pre_price", optional=True)
            condition_text = name + " " + (desc or "")
            member = bool(MEMBER.search(condition_text))
            multi = bool(MULTIBUY.search(condition_text))
            deposit = bool(DEPOSIT.search(condition_text))
            size, unit, count, quantity_diag, rate, computed, printed, confidence = _unit_price(
                price, obj.get("quantity"), desc, member, multi, deposit
            )
            offers.append((digest, catalog_id, oid, dealer, name, desc, price, prior, currency,
                           size, unit, count, quantity_diag, rate,
                           ("kg" if unit in ("g", "hg", "kg") else "l"
                            if unit in ("ml", "cl", "dl", "l") else None),
                           computed, printed, confidence, int(member), int(multi),
                           int(deposit), offer_frm, offer_till, None, None, None, "source_only"))
            if len(offers) > MAX_OFFERS:
                raise TjekSnapshotError("too many offers")
    if not offers:
        raise TjekSnapshotError("no offer records")
    return Snapshot(digest, fetched, fmt, raw, tuple(catalogs), tuple(offers))


def stage_snapshot(snapshot: Snapshot, db_path: Path, schema_path: Path) -> tuple[int, bool]:
    """Validate all data before connecting; replay of identical bytes is a no-op."""
    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(schema_path.read_text(encoding="utf-8"))
        with db:
            return len(snapshot.offers), insert_snapshot(db, snapshot)


def insert_snapshot(db: sqlite3.Connection, snapshot: Snapshot) -> bool:
    """Insert a validated snapshot inside the caller's transaction.

    Returns False (and writes nothing) when identical bytes are already staged.
    """
    if db.execute("SELECT 1 FROM tjek_snapshots WHERE sha256=?",
                  (snapshot.sha256,)).fetchone():
        return False
    db.execute("""INSERT INTO tjek_snapshots
               (sha256,format,fetched_at,raw_payload,raw_bytes)
               VALUES (?,?,?,?,?)""",
               (snapshot.sha256, snapshot.format, snapshot.fetched_at,
                snapshot.raw_payload, len(snapshot.raw_payload)))
    db.executemany("""INSERT INTO tjek_catalogs
                   (snapshot_sha256,catalog_id,dealer_id,chain_name,label,
                    run_from,run_till,published_at,claimed_offers)
                   VALUES (?,?,?,?,?,?,?,?,?)""", snapshot.catalogs)
    db.executemany("""INSERT INTO tjek_offers
                   (snapshot_sha256,catalog_id,offer_id,dealer_id,heading,
                    description,price_ore,pre_price_ore,currency,quantity,unit,
                    pack_count,quantity_diagnostic,unit_price_ore,unit_price_basis,
                    computed_unit_price_ore,printed_unit_price_ore,unit_price_confidence,
                    member_condition,multibuy_condition,deposit_condition,run_from,
                    run_till,local_store_id,ean,nutrition_basis,review_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   snapshot.offers)
    return True
