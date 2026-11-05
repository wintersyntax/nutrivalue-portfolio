#!/usr/bin/env python3
"""Fetch just KIWI's public weekly price-check page into PRIVATE review staging.

No LLM, authentication, bulk crawl, product matching or automatic ranking.
Use --input-html for deterministic local tests and offline reproduction.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.kiwi_pricecheck import (  # noqa: E402
    MAX_HTML_BYTES, SOURCE_URL, PriceCheckError, stage_html,
)
SCHEMA = ROOT / "analytics/kiwi_pricecheck_staging.sql"


def fetch_once() -> bytes:
    req = urllib.request.Request(
        SOURCE_URL, headers={"User-Agent": "NutriValue personal research/0.1"}
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        final_url = urllib.parse.urlparse(response.geturl())
        if final_url.scheme != "https" or final_url.hostname not in ("kiwi.no", "www.kiwi.no"):
            raise PriceCheckError("unexpected redirect outside KIWI")
        if "text/html" not in response.headers.get("Content-Type", ""):
            raise PriceCheckError("source did not return HTML")
        raw = response.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            raise PriceCheckError("HTML exceeds 2 MB limit")
        return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-html", type=Path, help="Read a saved HTML copy instead of the network")
    parser.add_argument("--db", type=Path, default=ROOT / "data/private/nutrivalue.sqlite3")
    args = parser.parse_args(argv)
    try:
        data = args.input_html.read_bytes() if args.input_html else fetch_once()
        info, digest = stage_html(data, args.db, SCHEMA)
    except (OSError, UnicodeError, sqlite3.Error, ValueError, PriceCheckError) as exc:
        print(f"KIWI price-check pilot: RED - {exc}", file=sys.stderr)
        return 1
    print(f"KIWI source week: {info.year}-W{info.week:02d}; through {info.valid_through}.")
    print(f"Staged {len(info.rows)} source-only rows ({sum(r.candidate_protein for r in info.rows)} possible protein-food names) into PRIVATE SQLite.")
    print(f"Snapshot SHA-256: {digest}")
    print("Nothing marked verified; no nutrition ranking or local-store price implied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
