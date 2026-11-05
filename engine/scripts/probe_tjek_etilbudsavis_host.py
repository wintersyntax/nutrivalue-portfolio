#!/usr/bin/env python3
"""TASK-033: exactly ONE bounded owner-authorized eTilbudsavis/Tjek v2 probe.

The owner reports official written Tjek permission for automated private
NutriValue use (TASK-015). One GET; no retries, no authentication workaround.
Save only to data/private/tjek/api-view-compare, mode 0600, never overwrite.
Print aggregate field counts only; no offer names, amounts, EANs, raw JSON.
No DB/price writes. The response may be a PARTIAL page, not full coverage.

  ./scripts/ci-python scripts/probe_tjek_etilbudsavis_host.py --live \
    --original data/private/tjek/weekly/2026-W42/extra-compare-20261016T004643Z/W8l7eqAB.json \
    --catalog-id W8l7eqAB --dealer-id 80742m
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_offline import parse_snapshot  # noqa: E402
from nutrivalue.providers.tjek_api_schema_compare import (  # noqa: E402
    _field_counts, compare_api_views,
)

HOST = "api.etilbudsavis.dk"
PAGE_LIMIT = 25
BYTE_LIMIT = 1_000_000
TIMEOUT_SECONDS = 12
PRIVATE_ROOT = ROOT / "data/private/tjek"
SOURCE_PAGE = "v2/offers"


class ProbeError(ValueError):
    """Bounded host probe fails closed without logging private contents."""


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{2,90}", value):
        raise ProbeError("invalid source identity")
    return value


def _read_original(path: Path, private_root: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ProbeError("original source absent or symlink")
    source = path.resolve(strict=True)
    source.relative_to(private_root.resolve(strict=True))
    if source.suffix != ".json" or source.stat().st_size > 1_000_000:
        raise ProbeError("original source invalid size/type")
    with source.open("rb") as fp:
        raw = fp.read(1_000_001)
    # Existing TASK-023 validation is mandatory before ANY HTTP request.
    parse_snapshot(raw, source.name)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ProbeError("original source is not an envelope")
    return value


def _one_response(*, catalog_id: str, dealer_id: str,
                  opener=urllib.request.urlopen) -> bytes:
    catalog_id, dealer_id = _identifier(catalog_id), _identifier(dealer_id)
    params = urllib.parse.urlencode(
        {"catalog_id": catalog_id, "limit": PAGE_LIMIT, "offset": 0})
    req = urllib.request.Request(
        f"https://{HOST}/{SOURCE_PAGE}?{params}",
        headers={
            "Accept": "application/json",
            "User-Agent": "NutriValue-personal-Tjek-schema-comparison/0.1",
        },
        method="GET",
    )
    try:
        with opener(req, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ProbeError(f"host returned HTTP {response.status}; no retry")
            mime = response.headers.get("Content-Type", "")
            if mime.split(";")[0].strip().lower() != "application/json":
                raise ProbeError("host returned non-JSON MIME; no retry")
            raw = response.read(BYTE_LIMIT + 1)
    except urllib.error.HTTPError as exc:
        raise ProbeError(f"host HTTP {exc.code}; stopped without retry") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProbeError("host network error; stopped without retry") from None
    if not raw or len(raw) > BYTE_LIMIT:
        raise ProbeError("empty/oversized response; stopped without retry")
    return raw


def probe_once(*, original: dict, catalog_id: str, dealer_id: str,
               private_root: Path, opener=urllib.request.urlopen,
               now=None) -> dict:
    """One real or mocked HTTP GET and private immutable save.

    A recognized list is archived even if an API host omits required
    normalized identity keys; this is precisely what schema comparison
    must detect. No synthetic field injection into the raw response.
    """
    catalog_id, dealer_id = _identifier(catalog_id), _identifier(dealer_id)
    compare_api_views(original, catalog_id=catalog_id, dealer_id=dealer_id)
    raw = _one_response(catalog_id=catalog_id, dealer_id=dealer_id,
                        opener=opener)
    try:
        rows = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ProbeError("invalid JSON response; no retry") from None
    if (not isinstance(rows, list) or len(rows) > PAGE_LIMIT
            or any(not isinstance(row, dict) for row in rows)):
        raise ProbeError("host returned unexpected v2 offers shape")
    if any(row.get("catalog_id") not in (None, catalog_id)
           or row.get("dealer_id") not in (None, dealer_id)
           for row in rows):
        raise ProbeError("host returned foreign catalog/dealer rows")

    root = private_root.resolve(strict=True)
    folder = private_root / "api-view-compare"
    if folder.is_symlink():
        raise ProbeError("private destination is a symlink")
    folder.mkdir(parents=False, mode=0o700, exist_ok=True)
    folder = folder.resolve(strict=True)
    folder.relative_to(root)
    stamp = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"etilbudsavis-v2-{catalog_id}-{stamp}.json"
    target = folder / filename
    # O_EXCL and 0600 ensure private raw bytes are never overwritten.
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                           0o600), "wb") as dst:
        dst.write(raw)
    shape_state = "comparison_shape_compatible"
    match = {}
    try:
        match = compare_api_views(
            original, catalog_id=catalog_id, dealer_id=dealer_id,
            other_response=rows)
    except ValueError:
        shape_state = "second_host_fields_not_original_v2_shape"
    if match:
        match.pop("original_field_counts", None)
        match.pop("comparison_field_counts", None)
    return {
        "status": shape_state,
        "http_requests": 1,
        "offers_sampled": len(rows),
        "page_filled_cap_coverage_unknown": len(rows) == PAGE_LIMIT,
        "source_only": True,
        "rank_eligible": False,
        "private_saved_relative": "data/private/tjek/api-view-compare/" + filename,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "optional_field_counts": _field_counts(rows),
        "comparison": match,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="required: exactly one real GET to second Tjek host")
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--catalog-id", required=True)
    parser.add_argument("--dealer-id", required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required; no implicit network access")
    try:
        original = _read_original(args.original, PRIVATE_ROOT)
        report = probe_once(original=original, catalog_id=args.catalog_id,
                            dealer_id=args.dealer_id,
                            private_root=PRIVATE_ROOT)
    except (ValueError, TypeError, OSError, UnicodeError) as exc:
        # Do not log source names, raw errors containing URLs, product data.
        print("TJEK SECOND HOST PROBE RED: " + str(exc)
              if isinstance(exc, ProbeError)
              else "TJEK SECOND HOST PROBE RED: " + type(exc).__name__,
              file=sys.stderr)
        return 1
    print("TJEK SECOND HOST PROBE — ONE GET, PRIVATE SOURCE_ONLY")
    for k, value in report.items():
        if k == "optional_field_counts":
            print("  optional_field_counts:")
            for name, n in sorted(value.items()):
                print(f"    {name}={n}")
        elif k == "comparison":
            print("  comparison:")
            for name, n in sorted(value.items()):
                if isinstance(n, (str, int, bool, type(None))):
                    print(f"    {name}={n}")
        else:
            print(f"  {k}: {value}")
    print("Same provider upstream; NOT independent shelf/checkout proof.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
