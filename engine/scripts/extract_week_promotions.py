#!/usr/bin/env python3
"""TASK-055: read every page of a saved weekly run with the chosen model pair
and store the promotions in the PRIVATE offer database.

Default = plan only (reads the public OpenRouter model list, no paid calls).
  ./scripts/ci-python scripts/extract_week_promotions.py \\
      --run-dir data/private/tjek/weekly/2026-W43/run-20261019T225318Z
  ... --execute        # PAID, budget-capped and resumable: read pages, retry
                       # invalid answers once, cross-check disputes, import
  ... --import-only    # no network: build and import from saved answers
  ... --reference data/private/tjek/promotions-reference/2026-W44.json  # also score
Measurement on a page set (never imported):
  ... --page-set data/private/tjek/page-sets/2026-W42/manifest.json \\
      --reference data/private/tjek/promotions-reference/2026-W42.json --execute

RapidOCR evidence needs the optional package, e.g.
  uv run --no-project --python 3.12 --with rapidocr==3.10.0 --with onnxruntime==1.31.0 \\
      python scripts/extract_week_promotions.py ...
Without it Tesseract (if installed) or no OCR is used. OCR is evidence, and a
cross-checked item is only accepted when OCR confirms its badge.
The run itself must already be imported (scripts/import_tjek_week.py).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.page_promotions import PROMPT_VERSION  # noqa: E402
from nutrivalue.page_sets import PageSetError, load, run_pages  # noqa: E402
from nutrivalue.promotion_benchmark import BenchmarkError, pairwise, run, score  # noqa: E402
from nutrivalue.promotion_extraction import (  # noqa: E402
    PAIR, ExtractionError, OcrLookup, build, crosscheck_plan, import_extraction, report,
    retry_candidates, run_crosschecks, score_final, set_aside_for_retry,
)
from nutrivalue.providers.openrouter import (  # noqa: E402
    OpenRouterClient, OpenRouterError, api_key, fetch_models,
)
from nutrivalue.standing_benefits import load_registry  # noqa: E402

PRIVATE = ROOT / "data" / "private"
REGISTRY = ROOT / "data" / "reference" / "standing_benefits.json"
VERSION_DIR = re.sub(r"[^A-Za-z0-9._-]+", "-", PROMPT_VERSION)


def _private(path: Path, cli, name: str) -> Path:
    full = path.resolve()
    if PRIVATE.resolve() not in full.parents:
        cli.error(f"{name} must be inside data/private/")
    return full


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    source = cli.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-dir", type=Path)
    source.add_argument("--page-set", type=Path, help="page-set manifest (measurement only)")
    cli.add_argument("--out", type=Path, help="default data/private/tjek/promotions/...")
    cli.add_argument("--db", type=Path, default=PRIVATE / "offers.sqlite3")
    cli.add_argument("--budget-usd", default="1.00")
    cli.add_argument("--interval-s", type=float, default=1.0)
    cli.add_argument("--reference", type=Path, help="optional blind reference to score")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="make PAID calls, then import")
    mode.add_argument("--import-only", action="store_true", help="no network: build and import")
    args = cli.parse_args(argv)
    try:
        cap = Decimal(args.budget_usd)
    except InvalidOperation:
        cli.error("--budget-usd must be a decimal number")
    if not Decimal("0.01") <= cap <= Decimal("5"):
        cli.error("--budget-usd must be between 0.01 and 5")
    if not 0 <= args.interval_s <= 30:
        cli.error("--interval-s must be between 0 and 30")
    if args.run_dir:
        run_dir = _private(args.run_dir, cli, "--run-dir")
        default_out = PRIVATE / "tjek" / "promotions" / run_dir.parent.name / run_dir.name
        run_id = None
    else:
        manifest = _private(args.page_set, cli, "--page-set")
        default_out = PRIVATE / "tjek" / "promotions" / "page-sets" / manifest.parent.name
        run_id = f"page-set:{manifest.parent.name}"
    out = _private(args.out or default_out / VERSION_DIR, cli, "--out")
    db = _private(args.db, cli, "--db")
    reference = (json.loads(_private(args.reference, cli, "--reference").read_text())
                 if args.reference else None)
    try:
        pages = (run_pages(run_dir, "all") if args.run_dir
                 else sorted(load(manifest)["pages"], key=lambda p: (p["catalog_id"], p["page"])))
        registry = load_registry(REGISTRY)
    except (PageSetError, OSError, KeyError, ValueError) as exc:
        print(f"RED: cannot read pages or the standing-benefit registry: {exc}", file=sys.stderr)
        return 1
    # OCR depends only on the image: one cache per run/page set, shared by prompt versions.
    ocr_dir = out / "ocr" if args.out else _private(default_out / "ocr", cli, "--out")
    ocr = OcrLookup(pages, ocr_dir)

    if not args.import_only:
        try:
            catalogue = fetch_models()
            client = (OpenRouterClient(api_key())
                      if args.execute else None)
            common = dict(out_dir=out, cap_usd=cap, catalogue=catalogue, client=client,
                          execute=args.execute, interval_s=args.interval_s)
            log = run(pages, list(PAIR), **common)
            print(f"Pages: {len(pages)}; planned calls: {log['planned_calls']}; remaining: "
                  f"{log['remaining_calls']}; worst-case reservations "
                  f"${log['max_reserved_estimate_usd']} (cap ${cap} enforced before every call)")
            if not args.execute:
                print("PLAN ONLY — no paid calls. Add --execute to run. Retries and "
                      "cross-checks are planned after the pages are read.")
                return 0
            retry = retry_candidates(pages, out)
            for model, page in retry:
                set_aside_for_retry(out, model, page)
            if retry:
                again = run([p for _, p in retry], list(PAIR), **common)
                print(f"Retried invalid answers: {again['executed_calls']}")
            plan = crosscheck_plan(pages, out, PAIR, registry)
            checks = run_crosschecks(plan, out, catalogue=catalogue, client=client,
                                     cap_usd=cap, execute=True, interval_s=args.interval_s)
            print(f"Executed paid calls: {log['executed_calls']}; cross-checks planned "
                  f"{checks['planned_checks']}, executed {checks['executed_calls']}")
        except (BenchmarkError, OpenRouterError) as exc:
            print(f"RED: {exc}", file=sys.stderr)
            return 1

    try:
        built = build(pages, out, ocr_lookup=ocr, registry=registry,
                      run_id=run_id or f"{run_dir.parent.name}/{run_dir.name}")
        result = (import_extraction(built, db, ROOT / "analytics") if args.run_dir
                  else {"imported": False, "extraction_id": built["extraction"]["extraction_id"]})
    except (ExtractionError, OSError, sqlite3.Error) as exc:
        print(f"RED: {exc}", file=sys.stderr)
        return 1
    lines = report(built)
    (out / "report.md").write_text("\n".join(lines) + "\n")
    (out / "review-queue.json").write_text(
        json.dumps(built["review_queue"], indent=1, ensure_ascii=False))
    print("\n".join(lines))
    if args.run_dir:
        print(f"{'Imported' if result['imported'] else 'Already present'}: "
              f"{result['extraction_id']}")
    else:
        print("Page set: measurement only, not imported.")

    if reference is not None:
        keys = {(p["catalog_id"], p["page"]): p["sha256"] for p in pages}
        if any(keys.get((p["catalog_id"], p["page"])) != p["image_sha256"]
               for p in reference["pages"]):
            print("RED: reference pages/hashes do not match these pages", file=sys.stderr)
            return 1
        scored = score(reference, list(PAIR), out, ocr_dir=ocr_dir, pages=pages)
        pair = pairwise(reference, list(PAIR), out, ocr_lookup=ocr)[0]
        final = score_final(built, reference)
        (out / "blind-score.json").write_text(json.dumps(
            {"models": scored, "pair": pair, "pipeline": final}, indent=1))
        for model, row in scored.items():
            print(f"Raw {model}: precision {row['promotion_precision']}, recall "
                  f"{row['promotion_recall']}, false {row['false_assertions']}, "
                  f"missed {row['missed']}")
        print(f"Pipeline: required auto {final.get('required_auto', 0)}, required in review "
              f"{final.get('required_in_review', 0)}, required missing "
              f"{final.get('required_missing', 0)}, REQUIRED REFUTED "
              f"{final.get('required_refuted', 0)}, AUTO WRONG {final.get('auto_wrong', 0)}, "
              f"wrong items refuted {final.get('wrong_refuted', 0)}, wrong items left for "
              f"review {final.get('wrong_in_review', 0)}")
    print(f"Private results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
