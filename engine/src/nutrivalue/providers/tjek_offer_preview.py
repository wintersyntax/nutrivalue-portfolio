"""TASK-031: PRIVATE read-only presentation of saved source-only Tjek offers.

Never contacts Tjek, writes a database, changes review state or verifies
individual shop prices. Offered rates are displayed only when source print
and calculated value agree and no pricing condition hides their meaning.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from nutrivalue.providers.tjek_offline import CHAINS

MAX_ROWS = 20_000
MAX_DISPLAY = 100
RELIABLE_PRINT_STATE = "source_print_consistent"


class TjekPreviewError(ValueError):
    """Unsafe arguments or broken private Tjek staging database."""


def _time(value: str) -> datetime:
    if not isinstance(value, str):
        raise TjekPreviewError("timestamps must be timezone-aware ISO-8601 strings")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TjekPreviewError("timestamps must be timezone-aware ISO-8601 strings") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TjekPreviewError("timestamps must include a timezone")
    return parsed


def _money(ore) -> str:
    if ore is None:
        return "unknown"
    if type(ore) is not int or ore <= 0:
        raise TjekPreviewError("invalid source price in private staging")
    return f"{ore // 100},{ore % 100:02d} NOK"


def _date_window(row, at: datetime) -> str:
    fetched = _time(row["fetched_at"])
    if fetched > at:
        return "fetched_later"
    starts = (_time(row["catalog_from"]), _time(row["run_from"]))
    ends = (_time(row["catalog_till"]), _time(row["run_till"]))
    if any(start >= end for start, end in zip(starts, ends)):
        raise TjekPreviewError("invalid source validity in private staging")
    if at < max(starts):
        return "future"
    if at >= min(ends):
        return "expired"
    return "current"


def _pack(row) -> str:
    if row["quantity_diagnostic"] != "exact_quantity":
        return "unknown"
    quantity, unit, pieces = row["quantity"], row["unit"], row["pack_count"]
    if not isinstance(quantity, str) or not quantity or not isinstance(unit, str):
        raise TjekPreviewError("invalid exact source quantity in private staging")
    if type(pieces) is not int or not 1 <= pieces <= 1000:
        raise TjekPreviewError("invalid pack count in private staging")
    return f"{pieces} x {quantity} {unit}" if pieces != 1 else f"{quantity} {unit}"


def _conditions(row) -> str:
    flags = (
        ("member", row["member_condition"]),
        ("multi-buy", row["multibuy_condition"]),
        ("pant", row["deposit_condition"]),
    )
    if any(value not in (0, 1) or type(value) is not int for _, value in flags):
        raise TjekPreviewError("invalid pricing condition in private staging")
    return ", ".join(name for name, value in flags if value) or "none"


def _rate(row, *, conditioned: bool) -> str:
    if conditioned or row["unit_price_confidence"] != RELIABLE_PRINT_STATE:
        return "withheld"
    if row["quantity_diagnostic"] != "exact_quantity":
        return "withheld"
    basis, ore = row["unit_price_basis"], row["unit_price_ore"]
    if basis not in ("kg", "l") or ore is None:
        return "withheld"
    return f"{_money(ore)}/{basis}"


def _source_coverage(row) -> str:
    expected, actual = row["claimed_offers"], row["observed_offers"]
    if type(actual) is not int or actual < 1:
        raise TjekPreviewError("invalid staged catalogue count")
    if expected is None:
        return f"unproven ({actual}/?)"
    if type(expected) is not int or expected < 0:
        raise TjekPreviewError("invalid source-declared offer count")
    return ("declared_match" if expected == actual else
            f"partial ({actual}/{expected})" if actual < expected else
            f"mismatch ({actual}/{expected})")


SQL = """
SELECT o.dealer_id, c.chain_name, o.heading, o.price_ore, o.quantity,
       o.unit, o.pack_count, o.quantity_diagnostic,
       o.unit_price_ore, o.unit_price_basis, o.unit_price_confidence,
       o.member_condition, o.multibuy_condition, o.deposit_condition,
       o.run_from, o.run_till, o.review_state,
       o.catalog_id, o.offer_id, o.snapshot_sha256,
       c.run_from AS catalog_from, c.run_till AS catalog_till,
       c.claimed_offers, s.fetched_at,
       (SELECT COUNT(*) FROM tjek_offers other
        WHERE other.snapshot_sha256=o.snapshot_sha256
          AND other.catalog_id=o.catalog_id) AS observed_offers
FROM tjek_offers o
JOIN tjek_catalogs c
  ON c.snapshot_sha256=o.snapshot_sha256 AND c.catalog_id=o.catalog_id
JOIN tjek_snapshots s ON s.sha256=o.snapshot_sha256
ORDER BY o.dealer_id, o.catalog_id, o.offer_id, s.fetched_at DESC
LIMIT ?
"""


def preview_offers(db_path: Path, *, as_of: str, include_inactive: bool = False,
                   limit: int = 20) -> dict:
    """Return strictly read-only source-only rows with bounded display counts."""
    at = _time(as_of)
    if type(include_inactive) is not bool:
        raise TjekPreviewError("include_inactive must be boolean")
    if type(limit) is not int or not 1 <= limit <= MAX_DISPLAY:
        raise TjekPreviewError("limit must be an integer from 1 to 100")
    path = Path(db_path)
    if not path.is_file():
        raise TjekPreviewError("private Tjek SQLite database is missing")
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            source = db.execute(SQL, (MAX_ROWS + 1,)).fetchall()
    except sqlite3.Error as exc:
        raise TjekPreviewError("cannot read private Tjek staging schema") from exc
    if len(source) > MAX_ROWS:
        raise TjekPreviewError("too many saved rows for bounded preview")

    latest = {}
    for row in source:
        if row["dealer_id"] not in CHAINS or row["chain_name"] != CHAINS[row["dealer_id"]]:
            raise TjekPreviewError("unexpected source chain in staging")
        if row["review_state"] != "source_only":
            raise TjekPreviewError("non-source-only price in Tjek staging")
        key = (row["dealer_id"], row["catalog_id"], row["offer_id"])
        previous = latest.get(key)
        if previous is None or (_time(row["fetched_at"]), row["snapshot_sha256"]) > (
                _time(previous["fetched_at"]), previous["snapshot_sha256"]):
            latest[key] = row

    rendered = []
    for row in latest.values():
        state = _date_window(row, at)
        if state != "current" and not include_inactive:
            continue
        conditions = _conditions(row)
        # Check the source price even if a row would be hidden by a display cap.
        amount = _money(row["price_ore"])
        rendered.append({
            "chain": row["chain_name"],
            "product": row["heading"],
            "advertised_price": amount,
            "package": _pack(row),
            "unit_rate": _rate(row, conditioned=conditions != "none"),
            "conditions": conditions,
            "rate_check": row["unit_price_confidence"],
            "quantity_check": row["quantity_diagnostic"],
            "window": state,
            "valid_from": row["run_from"],
            "valid_until": row["run_till"],
            "coverage": _source_coverage(row),
            "source": "source_only",
        })
    rendered.sort(key=lambda item: (
        item["chain"].casefold(), item["product"].casefold(),
        item["valid_from"], item["advertised_price"]
    ))
    return {
        "as_of": at.isoformat(),
        "total": len(rendered),
        "shown": min(limit, len(rendered)),
        "limited": len(rendered) > limit,
        "rows": rendered[:limit],
        "message": ("Saved advertised offers only (source_only). "
                    "Not verified local shelf prices, exact EAN/nutrition, "
                    "complete nine-chain coverage or permission to fetch."),
        "network_requests": 0,
        "database_writes": 0,
    }


def _safe_title(value: str) -> str:
    """Never pass source-supplied control sequences/newlines to the terminal."""
    if not isinstance(value, str):
        raise TjekPreviewError("invalid source heading")
    return "".join(" " if ord(char) < 32 or ord(char) == 127 else char
                   for char in value[:100]).strip()


def format_table(report: dict) -> str:
    """Compact, readable blocks rather than a terminal-width SQL dump."""
    rows = report["rows"]
    if not rows:
        return ("No saved offers satisfy the requested source-validity window.\n"
                "Try --include-inactive to review archived source-only offers.\n"
                + report["message"])
    lines = [
        f"TJEK PRIVATE PREVIEW — as of {report['as_of']}",
        f"Showing {report['shown']}/{report['total']} source-only offers"
        + (" (limited)" if report["limited"] else ""),
    ]
    for n, row in enumerate(rows, 1):
        lines.extend([
            "",
            f"{n}. {row['chain']} — {_safe_title(row['product'])}",
            f"   Advertised: {row['advertised_price']} | Pack: {row['package']}",
            f"   NOK/kg or NOK/l: {row['unit_rate']} | Rate check: {row['rate_check']}",
            f"   Conditions: {row['conditions']} | Quantity check: {row['quantity_check']}",
            f"   Valid: {row['valid_from']} → {row['valid_until']} ({row['window']})",
            f"   Saved catalog: {row['coverage']} | source_only",
        ])
    lines.extend([
        "",
        report["message"],
        "No API requests. No database writes. Unchecked rates are withheld.",
    ])
    return "\n".join(lines)
