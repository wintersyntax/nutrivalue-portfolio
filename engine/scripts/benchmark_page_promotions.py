#!/usr/bin/env python3
"""TASK-053/056: compare OpenRouter vision models on catalogue pages.

Default = plan only: reads the public OpenRouter model list (free) and prints
the call plan and a worst-case reservation. Paid calls ONLY with --execute,
using OPENROUTER_API_KEY from the environment, within --budget-usd.

Pages come from a saved TASK-051 run (--run-dir, hotspot-free pages by
default) or from a page set (--page-set manifest.json, any pages, see
scripts/prepare_page_set.py). The reference must cover exactly those pages.

  ./scripts/ci-python scripts/benchmark_page_promotions.py \\
      --page-set data/private/tjek/page-sets/2026-W42/manifest.json \\
      --reference data/private/tjek/promotions-reference/2026-W42.json \\
      --models anthropic/claude-haiku-5.5,openai/gpt-6-luna --label v2a
  ... --execute          # paid, resumable, budget-capped
  ... --score            # re-score saved answers, no network
  ... --consistency v2a,v2b   # run-to-run stability of two labelled repeats
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.page_sets import PageSetError, load, run_pages  # noqa: E402
from nutrivalue.promotion_benchmark import (  # noqa: E402
    Budget, BenchmarkError, LABEL, _ocr_lookup, consistency, monitor, pairwise, rank, run,
    score,
)
from nutrivalue.providers.openrouter import (  # noqa: E402
    OpenRouterClient, OpenRouterError, api_key, fetch_models,
)

PRIVATE = ROOT / "data" / "private"
DEFAULT_MODELS = ("google/gemini-3.1-flash-lite", "google/gemini-2.5-flash-lite",
                  "qwen/qwen3.7-flash", "anthropic/claude-haiku-5.5", "openai/gpt-6-luna")


def _private(path: Path, cli, name: str) -> Path:
    full = path.resolve()
    if PRIVATE.resolve() not in full.parents:
        cli.error(f"{name} must be inside data/private/")
    return full


def _pages(args, cli) -> list[dict]:
    if args.page_set:
        manifest = _private(args.page_set, cli, "--page-set")
        pages = load(manifest)["pages"]
        for page in pages:
            if manifest.parent.resolve() not in Path(page["path"]).resolve().parents:
                raise PageSetError("page file escapes the page set directory")
        return sorted(pages, key=lambda p: (p["catalog_id"], p["page"]))
    return run_pages(_private(args.run_dir, cli, "--run-dir"), args.include)


def _table(report: dict, order: list[str]) -> list[str]:
    lines = ["| model | answered | invalid | item err | page class | precision | recall | false "
             "| missed | member err | OCR confirmed / number only / absent | cost USD |",
             "|" + "---|" * 12]
    for m in order:
        r = report[m]
        lines.append(f"| {m} | {r['answered']}/{r['pages']} | {r['invalid_json']} | "
                     f"{r['item_errors']} | {r['page_class_accuracy']} | "
                     f"{r['promotion_precision']} | {r['promotion_recall']} | "
                     f"{r['false_assertions']} | {r['missed']} | {r['member_flag_errors']} | "
                     f"{r['ocr_confirmed']} / {r['ocr_number_only']} / {r['ocr_absent']} "
                     f"({r['ocr_engine']}) | {r['cost_usd']} |")
    return lines


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    source = cli.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-dir", type=Path)
    source.add_argument("--page-set", type=Path, help="page-set manifest.json")
    cli.add_argument("--include", default="hotspot_free",
                     choices=("hotspot_free", "with_hotspots", "all"),
                     help="pages of --run-dir to use (default hotspot_free)")
    cli.add_argument("--reference", type=Path, required=True)
    cli.add_argument("--models", default=",".join(DEFAULT_MODELS))
    cli.add_argument("--out", type=Path)
    cli.add_argument("--label", default="", help="independent repeat, e.g. v2a / v2b")
    cli.add_argument("--budget-usd", default="2.00")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="make PAID calls")
    mode.add_argument("--score", action="store_true", help="score saved answers only")
    mode.add_argument("--consistency", metavar="LABEL_A,LABEL_B",
                      help="compare two labelled repeats (no network)")
    mode.add_argument("--release-uncertain", metavar="KEY",
                      help="owner decision: release ONE uncertain attempt (needs --reason)")
    cli.add_argument("--reason", default="")
    args = cli.parse_args(argv)
    try:
        cap = Decimal(args.budget_usd)
    except InvalidOperation:
        cli.error("--budget-usd must be a decimal number")
    if not Decimal("0.01") <= cap <= Decimal("5"):
        cli.error("--budget-usd must be between 0.01 and 5")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not 1 <= len(models) <= 6 or len(set(models)) != len(models):
        cli.error("--models needs 1..6 distinct model ids")
    if args.label and not LABEL.fullmatch(args.label):
        cli.error("--label must be 1-32 letters, digits, '-' or '_'")
    reference = json.loads(_private(args.reference, cli, "--reference").read_text())
    out = _private(args.out or PRIVATE / "tjek" / "promotion-benchmark" / reference["week"],
                   cli, "--out")

    if args.release_uncertain:
        try:
            Budget(out / "budget.json", cap).release(args.release_uncertain, args.reason)
        except BenchmarkError as exc:
            print(f"RED: {exc}", file=sys.stderr)
            return 1
        print(f"Released {args.release_uncertain}: {args.reason}")
        return 0

    try:
        pages = _pages(args, cli)
    except (PageSetError, OSError, KeyError, ValueError) as exc:
        print(f"RED: cannot read pages: {exc}", file=sys.stderr)
        return 1
    ref_keys = {(p["catalog_id"], p["page"]): p["image_sha256"] for p in reference["pages"]}
    page_keys = {(p["catalog_id"], p["page"]): p["sha256"] for p in pages}
    if ref_keys != page_keys:
        print("RED: reference pages/hashes do not match the selected pages", file=sys.stderr)
        return 1
    keys = sorted(page_keys)

    if args.consistency:
        labels = [x.strip() for x in args.consistency.split(",")]
        try:
            result = consistency(models, out, labels, keys)
        except BenchmarkError as exc:
            print(f"RED: {exc}", file=sys.stderr)
            return 1
        target = out / f"consistency-{labels[0]}-{labels[1]}.json"
        target.write_text(json.dumps(result, indent=1, ensure_ascii=False))
        print(f"| model | compared | identical | stability | class changes | only {labels[0]} "
              f"| only {labels[1]} | unanswered/invalid |\n|" + "---|" * 8)
        for model, row in result["models"].items():
            print(f"| {model} | {row['compared_pages']} | {row['identical_pages']} | "
                  f"{row['stability']} | {row['page_class_changes']} | {row['items_only_first']} | "
                  f"{row['items_only_second']} | {row['unanswered_or_invalid']} |")
        print(f"Private result: {target}")
        return 0

    if not args.score:
        try:
            catalogue = fetch_models()
            client = None
            if args.execute:
                client = OpenRouterClient(api_key())
            log = run(pages, models, out_dir=out, cap_usd=cap, catalogue=catalogue,
                      client=client, execute=args.execute, label=args.label)
        except (BenchmarkError, OpenRouterError) as exc:
            print(f"RED: {exc}", file=sys.stderr)
            return 1
        print(f"Pages: {len(pages)}; models: {len(models)}; label: {args.label or '-'}; "
              f"planned calls: {log['planned_calls']}; remaining: {log['remaining_calls']}; "
              f"sum of per-call worst-case reservations ${log['max_reserved_estimate_usd']} "
              f"(cap ${cap} is enforced before every call)")
        if not args.execute:
            print("PLAN ONLY — no paid calls. Add --execute to run.")
            return 0
        print(f"Executed paid calls: {log['executed_calls']}")

    ocr = _ocr_lookup(pages, out / "ocr")
    report = score(reference, models, out, ocr_dir=out / "ocr", pages=pages, label=args.label)
    order = rank(report)
    pairs = pairwise(reference, models, out, label=args.label, ocr_lookup=ocr)
    health = monitor(pairs[0]["pair"], out, keys, label=args.label, ocr_lookup=ocr) \
        if pairs else None
    target = out / "runs" / args.label if args.label else out
    target.mkdir(parents=True, exist_ok=True)
    (target / "report.json").write_text(json.dumps(
        {"label": args.label, "ranking": order, "models": report, "pairs": pairs,
         "monitoring": {k: v for k, v in health.items() if k != "review_queue"}
         if health else None}, indent=1, ensure_ascii=False))
    if health:
        (target / "review-queue.json").write_text(
            json.dumps(health["review_queue"], indent=1, ensure_ascii=False))
    lines = _table(report, order)
    (target / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"Ranking (fewest false assertions first): {', '.join(order)}")
    print("Best independent pairs (agreed wrong / agreed right / to review; agreement rate; "
          "OCR-confirmed share of agreed):")
    for row in pairs[:5]:
        print(f"  {row['pair'][0]} + {row['pair'][1]}: {row['agreed_wrong']} / "
              f"{row['agreed_correct']} (recall {row['agreed_recall']}) / "
              f"{row['disagreements_for_review']}; {row['agreement_rate']}; "
              f"{row['ocr_confirmed_share']}")
    if health:
        print(f"Monitoring ({' + '.join(health['pair'])}): agreement {health['agreement_rate']}, "
              f"OCR-confirmed agreed {health['ocr_confirmed_agreed']}/"
              f"{health['ocr_checked_agreed']}, review queue {health['review_queue_size']}")
    print(f"Private results: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
