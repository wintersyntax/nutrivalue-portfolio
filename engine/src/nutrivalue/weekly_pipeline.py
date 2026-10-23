"""TASK-024/054: one weekly run, the same locally and in the cloud job.

collect (Tjek, bounded) -> import into the offer database -> read every page
with the model pair (reusing answers for unchanged page images of earlier
runs this week) -> retry invalid answers -> cross-check -> import the
extraction -> Markdown report. All paths live under ``root``; the cloud job
syncs ``root`` with private GCS, a local run uses the repository itself.

``mode`` "monday" is the first capture of an ISO week; "thursday" captures
again (mid-week catalogues) and pays only for pages not read before.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from nutrivalue import promotion_crosscheck as xcheck
from nutrivalue.offer_database import import_run
from nutrivalue.page_promotions import PROMPT_VERSION
from nutrivalue.page_sets import run_pages
from nutrivalue.promotion_benchmark import result_path, run
from nutrivalue.promotion_extraction import (
    PAIR, OcrLookup, build, crosscheck_plan, import_extraction, report, retry_candidates,
    run_crosschecks, set_aside_for_retry,
)

MODES = ("monday", "thursday")
VERSION_DIR = PROMPT_VERSION.replace("@", "-")


class WeeklyRunError(RuntimeError):
    """The weekly run stopped; the message says where."""


def paths(root: Path) -> dict:
    private = Path(root) / "data" / "private"
    return {"private": private, "weekly": private / "tjek" / "weekly",
            "promotions": private / "tjek" / "promotions", "db": private / "offers.sqlite3",
            "reports": private / "tjek" / "reports"}


def promo_dir(root: Path, week: str, run_name: str) -> Path:
    return paths(root)["promotions"] / week / run_name / VERSION_DIR


def ocr_dir(root: Path, week: str, run_name: str) -> Path:
    return paths(root)["promotions"] / week / run_name / "ocr"


def _sha_by_page(run_dir: Path) -> dict:
    return {(p["catalog_id"], p["page"]): p["sha256"] for p in run_pages(run_dir, "all")}


def reuse_earlier_answers(root: Path, run_dir: Path, pair=PAIR) -> dict:
    """Copy answers and OCR of earlier runs this week for identical page images.

    Only the same catalogue, page number, image SHA-256 and prompt version are
    reused, so a changed page is always read again.
    """
    week, current = run_dir.parent.name, run_dir.name
    target, target_ocr = promo_dir(root, week, current), ocr_dir(root, week, current)
    wanted = _sha_by_page(run_dir)
    copied = {"answers": 0, "ocr": 0}
    for earlier in sorted(run_dir.parent.glob("run-*"), reverse=True):
        if earlier.name == current or not (earlier / "manifest.json").exists():
            continue
        try:
            shas = _sha_by_page(earlier)
        except (OSError, ValueError, KeyError):
            continue
        source, source_ocr = promo_dir(root, week, earlier.name), ocr_dir(root, week, earlier.name)
        for key, sha in wanted.items():
            if shas.get(key) != sha:
                continue
            for model in pair:
                old = result_path(source, "", model, *key)
                new = result_path(target, "", model, *key)
                if old.exists() and not new.exists():
                    saved = json.loads(old.read_text())
                    if (saved.get("image_sha256") == sha
                            and saved.get("prompt_version") == PROMPT_VERSION):
                        new.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(old, new)
                        copied["answers"] += 1
            name = f"{key[0]}_p{key[1]:03d}.json"
            if (source_ocr / name).exists() and not (target_ocr / name).exists():
                target_ocr.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_ocr / name, target_ocr / name)
                copied["ocr"] += 1
    return copied


def reuse_earlier_crosschecks(root: Path, run_dir: Path, plan: list[dict]) -> int:
    """Copy cross-checks of earlier runs for the same image and the same claims."""
    week, copied = run_dir.parent.name, 0
    target = promo_dir(root, week, run_dir.name)
    for item in plan:
        new = xcheck.path_for(target, item["verifier"], item["page"])
        if new.exists():
            continue
        for earlier in sorted(run_dir.parent.glob("run-*"), reverse=True):
            if earlier.name == run_dir.name:
                continue
            old = xcheck.path_for(promo_dir(root, week, earlier.name), item["verifier"],
                                  item["page"])
            if not old.exists():
                continue
            saved = json.loads(old.read_text())
            if (saved.get("image_sha256") == item["page"]["sha256"]
                    and saved.get("claims") == item["claims"]):
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(old, new)
                copied += 1
                break
    return copied


def spent_usd(out_dir: Path) -> str:
    """OpenRouter cost settled or still reserved by this run (reused answers cost nothing)."""
    ledger = Path(out_dir) / "budget.json"
    if not ledger.exists():
        return "0"
    total = Decimal("0")
    for attempt in json.loads(ledger.read_text())["attempts"]:
        if attempt["status"] == "settled":
            total += Decimal(attempt["actual_usd"])
        elif attempt["status"] not in ("rejected", "released_by_owner"):
            total += Decimal(attempt["reserved_usd"])
    return str(total)


def run_week(root: Path, *, mode: str, collect, catalogue: dict, client, cap_usd: Decimal,
             registry, schema_dir: Path, interval_s: float = 1.0, sleep=None,
             ocr_reader=None, import_run_fn=import_run) -> dict:
    """One weekly run under ``root``. ``collect(out_dir, allow_repeat)`` returns the
    collector report (scripts pass the bounded live Tjek collector)."""
    if mode not in MODES:
        raise WeeklyRunError("mode must be monday or thursday")
    where = paths(root)
    summary = {"mode": mode, "started_at": datetime.now(timezone.utc).isoformat()}
    collected = collect(where["weekly"], mode == "thursday")
    week, run_name = collected["week"], collected["run_directory"]
    run_dir = where["weekly"] / week / run_name
    summary.update(week=week, run=run_name, batch_status=collected["batch_status"],
                   offers=collected.get("total_saved_offers"))
    imported = import_run_fn(run_dir, where["db"], schema_dir)
    summary["offers_imported"] = imported["imported"]
    if collected.get("halted_reason"):
        raise WeeklyRunError(f"collector halted: {collected['halted_reason']}")
    pages = run_pages(run_dir, "all")
    if not pages:
        raise WeeklyRunError("the run saved no page images")
    out = promo_dir(root, week, run_name)
    summary["reused"] = reuse_earlier_answers(root, run_dir)
    extra = {} if sleep is None else {"sleep": sleep}
    common = dict(out_dir=out, cap_usd=cap_usd, catalogue=catalogue, client=client,
                  execute=True, interval_s=interval_s, **extra)
    log = run(pages, list(PAIR), **common)
    retry = retry_candidates(pages, out)
    for model, page in retry:
        set_aside_for_retry(out, model, page)
    if retry:
        run([p for _, p in retry], list(PAIR), **common)
    plan = crosscheck_plan(pages, out, PAIR, registry)
    summary["reused"]["crosschecks"] = reuse_earlier_crosschecks(root, run_dir, plan)
    checks = run_crosschecks(plan, out, catalogue=catalogue, client=client, cap_usd=cap_usd,
                             execute=True, interval_s=interval_s, **extra)
    lookup = (OcrLookup(pages, ocr_dir(root, week, run_name)) if ocr_reader is None
              else OcrLookup(pages, ocr_dir(root, week, run_name), reader=ocr_reader))
    built = build(run_dir, out, ocr_lookup=lookup, registry=registry)
    stored = import_extraction(built, where["db"], schema_dir)
    lines = report(built)
    ext = built["extraction"]
    summary.update(page_calls=log["executed_calls"], retried=len(retry),
                   crosscheck_calls=checks["executed_calls"],
                   extraction_imported=stored["imported"], pages=ext["pages"],
                   review_queue=ext["review_queue_size"], answers_cost_usd=ext["cost_usd"],
                   spent_usd=spent_usd(out),
                   ocr_engine=ext["ocr_engine"],
                   finished_at=datetime.now(timezone.utc).isoformat())
    report_file = where["reports"] / week / f"{run_name}.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    head = [f"<!-- {json.dumps(summary, sort_keys=True)} -->",
            f"Collector: {collected['batch_status']}; {collected.get('total_saved_offers')} "
            f"offers; mode {mode}; reused {summary['reused']}; paid this run "
            f"{summary['spent_usd']} USD", ""]
    report_file.write_text("\n".join(head + lines) + "\n")
    (out / "review-queue.json").write_text(
        json.dumps(built["review_queue"], indent=1, ensure_ascii=False))
    summary["report"] = str(report_file.relative_to(root))
    return summary
