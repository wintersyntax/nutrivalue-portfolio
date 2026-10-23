#!/usr/bin/env python3
"""PRIVATE offline TASK-041 label sampling / local ML baseline.

All source titles stay inside curses alternate screen, or only inside
explicitly authorized 0600 ignored data/private/ JSONL. NEVER paste screen
captures/transcripts of --review to ChatGPT, GitHub or CI logs.

Shareable count-only preview (no writes):
  ./scripts/ci-python scripts/review_offer_sample.py \
    --archive-dir data/private/tjek/weekly/2026-W42/run-20261016T031849Z

Owner-only interactive labeling (explicit PRIVATE output opt-in):
  ./scripts/ci-python scripts/review_offer_sample.py \
    --archive-dir data/private/tjek/weekly/2026-W42/run-20261016T031849Z \
    --review --labels data/private/tjek/task041-owner-review-01.jsonl

Evaluate rule vs local in-memory ML after labels exist (no network/download):
  ./scripts/ci-python scripts/review_offer_sample.py \
    --evaluate --labels data/private/tjek/task041-owner-review-01.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.offer_review import (  # noqa: E402
    ReviewError, aggregate_review, interactive_review,
    private_labels_write, sample_archives,
)
from nutrivalue.offer_ml import evaluate_labels, load_private_labels  # noqa: E402
from nutrivalue.providers.tjek_offline import CHAINS  # noqa: E402

PRIVATE_ROOT = ROOT / "data/private"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path,
                        help="existing saved Tjek weekly run directory, not a live URL")
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--review", action="store_true",
                         help="interactive alternate-screen labeling, explicit --labels required")
    options.add_argument("--evaluate", action="store_true",
                         help="offline rule-vs-ML evaluation of existing private labels")
    parser.add_argument("--labels", type=Path,
                        help="ignored private .jsonl; explicit read for evaluate/write for review")
    parser.add_argument("--per-bucket", type=int, default=2,
                        help="max headings per chain x rule status, 1..5; default 2")
    args = parser.parse_args(argv)
    try:
        if args.evaluate:
            if args.archive_dir is not None or args.labels is None:
                raise ReviewError("--evaluate requires --labels and no --archive-dir")
            reviewed = load_private_labels(args.labels, private_root=PRIVATE_ROOT)
            print(json.dumps(evaluate_labels(reviewed), ensure_ascii=False,
                             sort_keys=True, indent=2))
            return 0
        if args.archive_dir is None:
            raise ReviewError("require --archive-dir for review plan")
        if args.review != (args.labels is not None):
            raise ReviewError("label destination requires explicit --review and vice versa")
        if not args.archive_dir.is_dir():
            raise ReviewError("saved weekly archive directory unavailable")
        sources = [args.archive_dir / f"{dealer}.json" for dealer in CHAINS
                   if (args.archive_dir / f"{dealer}.json").is_file()]
        candidates, plan = sample_archives(sources, per_bucket=args.per_bucket)
        if not args.review:
            print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise ReviewError("owner review requires a real interactive terminal")
        # Source titles are shown only in curses alternate screen, not stdout
        # captured by a normal CI/Python pipe. Never record this TTY session.
        labels = interactive_review(candidates)
        if labels:
            private_labels_write(args.labels, labels, private_root=PRIVATE_ROOT)
        print(json.dumps(aggregate_review(labels, sampled=len(candidates)),
                         ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except ReviewError as exc:
        print(f"TASK-041 PRIVATE REVIEW: NOT GREEN - {exc}", file=sys.stderr)
        return 2
    except OSError:
        # A raw OS exception could contain the local PRIVATE source/label path.
        print("TASK-041 PRIVATE REVIEW: NOT GREEN - private filesystem operation failed",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
