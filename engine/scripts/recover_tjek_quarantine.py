#!/usr/bin/env python3
"""Offline validation of a PRIVATE TASK-032 .unvalidated Tjek quarantine.

Dry-run is the default; --apply creates an exclusive validated JSON sibling.
No Tjek GET, no SQLite writes, no modification of the original run manifest.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.tjek_weekly_collect import (  # noqa: E402
    CollectionError, recover_quarantine,
)

PRIVATE_WEEKLY = ROOT / "data/private/tjek/weekly"


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, required=True,
                     help="existing PRIVATE YYYY-Www/run-*/dealer.unvalidated file")
    cli.add_argument("--apply", action="store_true",
                     help="write a validated .json sibling only if absent")
    args = cli.parse_args(argv)
    try:
        result = recover_quarantine(archive_root=PRIVATE_WEEKLY,
                                    input_path=args.input, apply=args.apply)
    except (OSError, CollectionError) as exc:
        print(f"TJEK OFFLINE QUARANTINE: RED — {exc}", file=sys.stderr)
        return 1
    print(f"TJEK OFFLINE QUARANTINE: {result['status']} "
          f"— {result['chain']}, {result['catalogs']} catalogs, "
          f"{result['offers']} source_only offers")
    print(f"Source SHA-256: {result['sha256']}")
    print("No API requests. Original quarantined file and manifest unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
