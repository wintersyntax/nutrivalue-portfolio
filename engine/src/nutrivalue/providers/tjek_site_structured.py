"""TASK-033: bounded parser for eTilbudsavis server-rendered structured offers.

A saved page carries entity-escaped JSON {"data": [offers...]} in
successful app-data elements. Legacy literal script JSON is also supported.
This is a second representation of Tjek, never independent retailer price
evidence. No HTTP, JavaScript execution, positional joins or inferred prices.
Unknown layouts and incomplete or conflicting records fail closed.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
import re
from typing import Mapping

from nutrivalue.providers.tjek_pdf_link import _normalized
from nutrivalue.providers.tjek_api_schema_compare import OPTIONAL_SITE_KEYS

MAX_HTML_BYTES = 6_000_000
MAX_SITE_RECORDS = 500
# JSON serializers freely add whitespace around the key/colon/array.
# Match only literal JSON, not an escaped Next.js streaming string.
DATA_ARRAY_START = re.compile(r'\{\s*"data"\s*:\s*\[')
# Retain the existing single-literal assignment form without scanning
# arbitrary JS strings, comments, expressions or executable suffixes.
LITERAL_ASSIGNMENT = re.compile(
    r'(?:(?:const|let|var)\s+)?[A-Za-z_$][A-Za-z0-9_$]*'
    r'(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\s*=\s*')


class SiteShapeError(ValueError):
    pass


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self._in_script = False
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self._in_script = True
            self._parts = []

    def handle_data(self, data):
        if self._in_script:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._in_script:
            self.scripts.append("".join(self._parts))
            self._parts = []
            self._in_script = False


class _AppData(HTMLParser):
    """Collect only closed, text-only app-data with successful cache status.

    HTMLParser decodes character references exactly once in handle_data.
    Script bodies are raw text and never become app-data markup. Nested
    markup/comments invalidate a container instead of joining its fragments.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.payloads: list[str] = []
        self._depth = 0
        self._valid = False
        self._parts: list[str] = []
        self._count = 0

    def handle_starttag(self, tag, attrs):
        if self._depth:
            self._valid = False
        if tag == "app-data":
            self._count += 1
            if self._count > MAX_SITE_RECORDS:
                raise SiteShapeError("too many site data containers")
            if not self._depth:
                names = [key for key, _ in attrs]
                self._valid = (len(names) == len(set(names)) and
                               dict(attrs).get("data-status") == "success")
                self._parts = []
            self._depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self._depth:
            self._parts.append(data)

    def handle_comment(self, data):
        if self._depth:
            self._valid = False

    def handle_decl(self, decl):
        if self._depth:
            self._valid = False

    def handle_pi(self, data):
        if self._depth:
            self._valid = False

    def handle_endtag(self, tag):
        if not self._depth:
            return
        if tag != "app-data":
            self._valid = False
            return
        self._depth -= 1
        if not self._depth:
            if self._valid:
                self.payloads.append("".join(self._parts))
            self._parts = []


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SiteShapeError("duplicate site JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise SiteShapeError("nonfinite site JSON constant")


def _app_data_groups(text: str) -> list[dict]:
    parser = _AppData()
    parser.feed(text)
    parser.close()
    groups = []
    for body in parser.payloads:
        try:
            value = json.loads(body, object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
        except (ValueError, RecursionError) as exc:
            raise SiteShapeError("invalid site app-data JSON") from exc
        # Only the observed top-level data array is an offer container.
        # Other cached resources and nested metadata are not searched.
        if isinstance(value, dict) and isinstance(value.get("data"), list):
            groups.append(value)
    return groups


def _payloads_from_script(script: str) -> list[dict]:
    script = script.strip()
    assignment = LITERAL_ASSIGNMENT.match(script)
    if assignment:
        script = script[assignment.end():]
    if DATA_ARRAY_START.match(script) is None:
        return []
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
    try:
        value, end = decoder.raw_decode(script)
    except json.JSONDecodeError:
        return []
    except RecursionError as exc:
        raise SiteShapeError("site JSON nesting outside bounds") from exc
    if script[end:].strip() not in ("", ";"):
        return []
    rows = value["data"]
    if len(rows) > MAX_SITE_RECORDS or any(not isinstance(r, dict) for r in rows):
        raise SiteShapeError("site structured offer array invalid or oversized")
    if rows and all("publicId" in r and "publicationPublicId" in r
                    and "businessPublicId" in r for r in rows):
        return [value]
    return []


def _valid(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.astimezone(timezone.utc) if date.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _ore(amount: object) -> int | None:
    if amount is None or isinstance(amount, bool):
        return None
    try:
        n = Decimal(str(amount).replace(",", "."))
        if not n.is_finite() or not 0 < n < 100000:
            return None
        v = n * 100
        return int(v) if v == v.to_integral_value() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _complete_offer(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    for key in ("publicId", "publicationPublicId", "businessPublicId", "name"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            return False
    start, end = _valid(row.get("validFrom")), _valid(row.get("validUntil"))
    return (row.get("currencyCode") == "NOK" and _ore(row.get("price")) is not None
            and start is not None and end is not None and start <= end)


def extract_structured_offers(html_bytes: bytes) -> list[dict]:
    """Parse observed app-data JSON and legacy script arrays, strictly offline.

    app-data uses one HTML-entity decode followed by one JSON parse. No
    recursive string decoding, Next.js Flight decoder or JS interpreter.
    Required attributes must coexist in one object. Identical hydration
    repeats are deduplicated; conflicting IDs are never resolved by order.
    """
    if not isinstance(html_bytes, bytes) or not 0 < len(html_bytes) <= MAX_HTML_BYTES:
        raise SiteShapeError("invalid html size")
    try:
        text = html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SiteShapeError("site html not utf-8") from exc
    scripts = _Scripts()
    scripts.feed(text)
    groups = _app_data_groups(text)
    for script in scripts.scripts:
        groups.extend(_payloads_from_script(script))
    by_id: dict[str, dict] = {}
    candidates_by_id: dict[str, dict] = {}
    total = 0
    for group in groups:
        rows = group["data"]
        total += len(rows)
        if total > MAX_SITE_RECORDS:
            raise SiteShapeError("site structured record budget exceeded")
        seen = set()
        for row in rows:
            if (isinstance(row, dict) and isinstance(row.get("publicId"), str)
                    and all(key in row for key in
                            ("publicationPublicId", "businessPublicId"))):
                key = row["publicId"]
                if key in candidates_by_id and row != candidates_by_id[key]:
                    raise SiteShapeError("site structured records conflict")
                candidates_by_id[key] = row
            if not _complete_offer(row):
                continue
            key = row["publicId"]
            if key in seen:
                raise SiteShapeError("site structured array has duplicated IDs")
            seen.add(key)
            by_id[key] = row
    if not by_id:
        raise SiteShapeError("no supported complete site offers")
    return list(by_id.values())


def inspect_site_offers(original: Mapping, site_rows: list[dict], *,
                        catalog_id: str, dealer_id: str) -> dict[str, object]:
    """Filter one original catalog/chain, join only exact offer ID + identity."""
    if (not isinstance(original, Mapping) or
            not isinstance(original.get("entries"), list)):
        raise SiteShapeError("missing original archive")
    sources = []
    for entry in original["entries"]:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("catalog"), Mapping):
            raise SiteShapeError("invalid original entry")
        if entry["catalog"].get("id") == catalog_id:
            if entry["catalog"].get("dealer_id") != dealer_id:
                raise SiteShapeError("foreign catalog")
            sources.extend(entry.get("offers", []))
    ids = {}
    for item in sources:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            raise SiteShapeError("invalid original offer")
        if item["id"] in ids:
            raise SiteShapeError("duplicate original ID")
        ids[item["id"]] = item
    if not ids:
        raise SiteShapeError("original catalog missing")
    stats = Counter()
    stats["original_offers"] = len(ids)
    stats["site_records_unfiltered"] = len(site_rows)
    seen = set()
    for row in site_rows:
        if not isinstance(row, dict):
            raise SiteShapeError("invalid site record")
        if (row.get("publicationPublicId") != catalog_id
                or row.get("businessPublicId") != dealer_id):
            continue
        stats["same_catalog_site_records"] += 1
        key = row.get("publicId")
        if not isinstance(key, str) or not key:
            stats["missing_site_offer_id"] += 1
            continue
        if key in seen:
            raise SiteShapeError("duplicate site ID for selected catalog")
        seen.add(key)
        for field in OPTIONAL_SITE_KEYS:
            if field in row and row[field] is not None:
                stats["selected_nonnull:" + field] += 1
        if key not in ids:
            stats["site_id_not_in_original"] += 1
            continue
        candidate = ids[key]
        if (_normalized(candidate.get("heading") or "") !=
                _normalized(row.get("name") or "")):
            stats["same_id_heading_conflict"] += 1
            continue
        cf, ct = _valid(candidate.get("run_from")), _valid(candidate.get("run_till"))
        sf, st = _valid(row.get("validFrom")), _valid(row.get("validUntil"))
        if not all((cf, ct, sf, st)) or cf > st or sf > ct:
            stats["same_id_dates_conflict"] += 1
            continue
        stats["matched_original_id_name_dates"] += 1
        price = candidate.get("pricing")
        original_price = _ore(price.get("price")) if isinstance(price, Mapping) else None
        site_price = _ore(row.get("price"))
        if site_price is None or original_price is None:
            stats["matched_price_missing_one_side_review_only"] += 1
            continue
        if site_price != original_price or row.get("currencyCode") != "NOK":
            stats["matched_price_conflict_review_only"] += 1
            continue
        stats["matched_exact_price_review_only"] += 1
        if _ore(row.get("membershipPrice")) is not None:
            stats["matched_possible_member_field_review_only"] += 1
        get, pay = row.get("pieceCountGet"), row.get("pieceCountGetFor")
        if type(get) is int and type(pay) is int and 2 <= get <= 12 and 1 <= pay < get:
            stats["matched_get_n_pay_m_review_only"] += 1
    return {
        "status": "site_sample_only",
        "counters": dict(sorted(stats.items())),
        "rank_eligible": False,
        "source_only": True,
        "same_tjek_upstream": True,
        "no_store_checkout_verification": True,
    }
