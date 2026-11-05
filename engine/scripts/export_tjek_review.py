#!/usr/bin/env python3
"""TASK-041: export existing nine-chain offer text for PRIVATE owner review.

Default prints COUNTS ONLY, without writing a file:
  ./scripts/ci-python scripts/export_tjek_review.py \
    --archive-dir data/private/tjek/weekly/2026-W42/run-20261016T031849Z

Owner-authorized local export (writes ONE exclusive 0600 JSONL file):
  ./scripts/ci-python scripts/export_tjek_review.py \
    --archive-dir data/private/tjek/weekly/2026-W42/run-20261016T031849Z \
    --write --output data/private/tjek/task041-category-review.jsonl

The output may be uploaded to the existing private NutriValue GitHub
repository as an unreferenced blob, never committed to the code branch/PR. No provider HTTP, new snapshot or DB writes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.offer_review_export import (  # noqa: E402
    ExportError, assemble_review_export, write_private_export,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive-dir", type=Path, required=True,
                    help="already saved private nine-chain batch; no network")
    ap.add_argument("--write", action="store_true",
                    help="explicit permission to write the private JSONL once")
    ap.add_argument("--output", type=Path,
                    help="exclusive output path under ignored data/private/")
    args = ap.parse_args(argv)
    try:
        if args.write != (args.output is not None):
            raise ExportError("--write and --output must be supplied together")
        payload, summary = assemble_review_export(args.archive_dir)
        if args.write:
            write_private_export(args.output, payload,
                                 private_root=ROOT / "data/private")
            summary["private_export_created"] = True
        else:
            summary["private_export_created"] = False
    except ExportError as exc:
        print(f"TASK-041 REVIEW EXPORT: NOT GREEN - {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
