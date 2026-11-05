#!/usr/bin/env python3
"""Import annual official Matvaretabellen JSON into isolated reference-food tables.

Default: read four live official endpoints, once each (no key required).
For offline imports place foods.json, food-groups.json, nutrients.json and
sources.json in --input-dir. Never commit bulk provider responses by accident.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.matvaretabellen import (  # noqa: E402
    MatvaretabellenError, fetch_payloads, import_payloads,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locale", choices=["nb", "en"], default="nb")
    parser.add_argument("--db", type=Path, default=ROOT / "data/private/nutrivalue.sqlite3")
    parser.add_argument("--input-dir", type=Path, default=None,
                        help="Directory containing four official named JSON files")
    args = parser.parse_args()
    try:
        result = import_payloads(
            args.db, fetch_payloads(args.locale, input_dir=args.input_dir), locale=args.locale,
        )
    except (MatvaretabellenError, OSError) as exc:
        print(f"Matvaretabellen import failed: {exc}", file=sys.stderr)
        return 1
    print(f"{result['status']}: snapshot={result['snapshot_id']} "
          f"foods={result['foods']} nutrients={result['nutrients']} "
          f"measurements={result['measurements']}")
    print(f"content_sha256={result['sha256']}")
    print("Attribution: Matvaretabellen / Mattilsynet — https://www.matvaretabellen.no/")
    print("Reference foods only; no SKU-price matches or verified shop prices were generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
