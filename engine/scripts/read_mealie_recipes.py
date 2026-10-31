#!/usr/bin/env python3
"""Read recipes from an explicitly configured Mealie instance (GET only).

Plan only by default. --execute requires --base-url or NV_MEALIE_URL and a
separately supplied MEALIE_API_TOKEN (or optional macOS Keychain entry).
--from-snapshot imports an existing local snapshot without network access.
Generated snapshots and SQLite files stay in ignored data/private/.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.mealie_read import (  # noqa: E402
    DEFAULT_BASE_URL, MealieError, MealieReader, api_token, import_snapshot, load_snapshot,
    report, save_snapshot,
)

PRIVATE = ROOT / "data" / "private"
SCHEMA = ROOT / "analytics" / "recipe_read_model.sql"


def _private(path: Path, cli, name: str) -> Path:
    full = path.resolve()
    if PRIVATE.resolve() not in full.parents:
        cli.error(f"{name} must be inside data/private/")
    return full


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--base-url", default=os.environ.get("NV_MEALIE_URL", DEFAULT_BASE_URL))
    cli.add_argument("--out", type=Path, default=PRIVATE / "mealie" / "snapshots")
    cli.add_argument("--db", type=Path, default=PRIVATE / "nutrivalue.sqlite3")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="read Mealie (GET only)")
    mode.add_argument("--from-snapshot", type=Path, help="import a saved snapshot offline")
    args = cli.parse_args(argv)
    out = _private(args.out, cli, "--out")
    db_path = _private(args.db, cli, "--db")

    if args.from_snapshot:
        folder = _private(args.from_snapshot, cli, "--from-snapshot")
    elif args.execute:
        if not args.base_url:
            cli.error("--execute requires --base-url or NV_MEALIE_URL")
        try:
            reader = MealieReader(args.base_url, api_token())
            folder = save_snapshot(reader, out)
        except (MealieError, OSError) as exc:
            print(f"RED: {exc}", file=sys.stderr)
            return 1
        print(f"Read {reader.host} with {reader.calls} GET calls; saved {folder}")
    else:
        print(f"PLAN ONLY — no network. --execute reads every recipe from {args.base_url or 'an explicitly configured instance'} "
              "with GET requests only (list pages + one call per recipe) and imports them.")
        return 0

    try:
        snapshot = load_snapshot(folder)
        with sqlite3.connect(db_path) as db:
            new = import_snapshot(db, snapshot, SCHEMA)
    except (MealieError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"RED: {exc}", file=sys.stderr)
        return 1
    lines = report(snapshot)
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"{'Imported' if new else 'Already present'}: snapshot {snapshot['snapshot_id'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
