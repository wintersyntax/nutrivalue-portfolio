"""TASK-055: weekly page-promotion extraction into the PRIVATE offer database.

Every page of a saved TASK-051 collector run is read independently by the
two chosen vision models (TASK-053/056). What happens to each item:

- both models report it identically (kind, numbers, member flag): accepted;
- it is a known permanent loyalty benefit (``standing_benefits``): standing;
- only one model reports it: the other model cross-checks it
  (``promotion_crosscheck``) and it is accepted only when that model
  confirms it, rejects a decoy and OCR confirms the badge; it is refuted
  when that model says it is not printed, rejects the decoy and OCR finds no
  such badge;
- otherwise, and for every page without a valid answer from both after one
  automatic retry: needs_review.

Paid calls go through the budget-capped, resumable, routed runner of
``promotion_benchmark``; building and importing work offline from the saved
answers. Dates always come from the catalogue; a promotion never rewrites an
offer price.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from nutrivalue import promotion_crosscheck as xcheck
from nutrivalue.page_promotions import PROMPT_VERSION, PromotionOutputError, ocr_status
from nutrivalue.page_sets import run_pages
from nutrivalue.promotion_benchmark import (
    Budget, BenchmarkError, _key, after_call, load_answer, monitor, ocr_lines_for, pair_items,
    paid_call, reservation, result_path, results_dir,
)
from nutrivalue.standing_benefits import normalize

PAIR = ("anthropic/claude-haiku-5.5", "openai/gpt-6-luna")
METHOD = f"vlm-pair:{PROMPT_VERSION}"
SCHEMAS = ("tjek_offer_staging.sql", "offer_database.sql", "promotion_extraction.sql")
AUTO_STATUSES = ("accepted", "crosscheck_accepted", "standing")


class ExtractionError(ValueError):
    """An extraction cannot be built or stored without guessing."""


def run_id_for(run_dir: Path) -> str:
    run_dir = Path(run_dir)
    return f"{run_dir.parent.name}/{run_dir.name}"


def extraction_id(run_id: str, pair=PAIR) -> str:
    return f"{run_id}|{PROMPT_VERSION}|{xcheck.VERSION}|{pair[0]}+{pair[1]}"


class OcrLookup:
    """Cached OCR lines per page; remembers which engine produced them."""

    def __init__(self, pages: list[dict], ocr_dir: Path, reader=ocr_lines_for):
        self.paths = {(p["catalog_id"], p["page"]): p["path"] for p in pages}
        self.ocr_dir, self.reader, self.engine = Path(ocr_dir), reader, None

    def __call__(self, key):
        if key not in self.paths:
            return None
        found = self.reader(Path(self.paths[key]),
                            self.ocr_dir / f"{key[0]}_p{key[1]:03d}.json")
        if found is None:
            return None
        self.engine = found[0]
        return found[1]


# --- automatic retry of a page without a valid answer -----------------------

def _invalid_dir(out_dir: Path, model: str, page: dict) -> Path:
    path = result_path(out_dir, "", model, page["catalog_id"], page["page"])
    return Path(out_dir) / "results-invalid" / path.parent.name / path.name


def retry_candidates(pages: list[dict], out_dir: Path, pair=PAIR) -> list[tuple[str, dict]]:
    """(model, page) whose saved answer is structurally invalid and not yet retried."""
    found = []
    for page in pages:
        for model in pair:
            saved, parsed, _ = load_answer(out_dir, "", model, page["catalog_id"], page["page"])
            if saved is not None and parsed is None and not _invalid_dir(out_dir, model,
                                                                         page).exists():
                found.append((model, page))
    return found


def set_aside_for_retry(out_dir: Path, model: str, page: dict) -> None:
    """Keep the invalid answer as evidence; the runner then asks once more."""
    source = result_path(out_dir, "", model, page["catalog_id"], page["page"])
    target = _invalid_dir(out_dir, model, page)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


# --- shared per-page view of both answers ------------------------------------

def _page_answers(page: dict, out_dir: Path, pair, registry) -> dict:
    found = {"saved": {}, "parsed": {}, "error": {}, "normalized": {}}
    for model in pair:
        saved, parsed, error = load_answer(out_dir, "", model, page["catalog_id"], page["page"])
        found["saved"][model], found["parsed"][model], found["error"][model] = saved, parsed, error
        found["normalized"][model] = normalize(parsed, page.get("chain") or "", registry)
    return found


def _disputes(normalized: dict, pair):
    a, b = pair
    pairs, only_a, only_b = pair_items(normalized[a], normalized[b])
    claims = {b: [x for x in only_a if "standing_id" not in x],      # b verifies a's items
              a: [x for x in only_b if "standing_id" not in x]}
    return pairs, only_a, only_b, claims


def crosscheck_plan(pages: list[dict], out_dir: Path, pair=PAIR, registry=()) -> list[dict]:
    """Every (verifier, page, numbered claims) the current answers call for."""
    plan = []
    for page in pages:
        answers = _page_answers(page, out_dir, pair, registry)
        if any(answers["parsed"][m] is None for m in pair):
            continue
        _, _, _, claims = _disputes(answers["normalized"], pair)
        for verifier in pair:
            if claims[verifier]:
                plan.append({"verifier": verifier, "page": page,
                             "claims": xcheck.claims_for(page, claims[verifier])})
    return plan


def run_crosschecks(plan: list[dict], out_dir: Path, *, catalogue: dict, client, cap_usd,
                    execute: bool, interval_s: float = 1.0, sleep=None) -> dict:
    """Paid, budget-capped cross-check calls (resumable: saved checks are reused)."""
    todo = [item for item in plan if not xcheck.path_for(out_dir, item["verifier"],
                                                         item["page"]).exists()]
    estimate = sum((reservation(catalogue[i["verifier"]]) for i in todo), Decimal("0"))
    log = {"planned_checks": len(plan), "remaining_checks": len(todo),
           "max_reserved_estimate_usd": str(estimate), "executed_calls": 0,
           "rate_limit_waits": 0}
    if not execute or not todo:
        return log
    budget = Budget(Path(out_dir) / "budget.json", cap_usd)
    extra = {} if sleep is None else {"sleep": sleep}
    for item in todo:
        page, verifier = item["page"], item["verifier"]
        image = Path(page["path"]).read_bytes()
        if hashlib.sha256(image).hexdigest() != page["sha256"]:
            raise BenchmarkError(f"page image hash mismatch: {page['catalog_id']} p{page['page']}")
        prompt = xcheck.build_prompt(page, item["claims"])
        call = paid_call(budget, f"xcheck|{verifier}|{page['catalog_id']}|{page['page']}",
                         verifier, catalogue[verifier], client, prompt, image, xcheck.SCHEMA, log,
                         interval_s=interval_s, **extra)
        xcheck.save(out_dir, verifier, page, item["claims"], prompt, call)
        after_call(verifier, call)
    return log


# --- build the rows of one extraction (no network) ---------------------------

def _source(source) -> tuple[list[dict], str | None]:
    if isinstance(source, (str, Path)):
        return run_pages(Path(source), "all"), run_id_for(Path(source))
    return list(source), None


def build(source, out_dir: Path, *, pair=PAIR, ocr_lookup=None, registry=(),
          run_id: str | None = None) -> dict:
    """Rows for one complete extraction from saved answers and cross-checks.

    ``source`` is a collector run directory or a list of page dicts (a page
    set, then ``run_id`` names it; such an extraction is for measurement and
    is not imported).
    """
    out_dir = Path(out_dir)
    pages, derived = _source(source)
    run_id = run_id or derived
    if not pages or not run_id:
        raise ExtractionError("no pages (or no run id) to build from")
    keys = [(p["catalog_id"], p["page"]) for p in pages]
    ext_id = extraction_id(run_id, pair)
    a, b = pair
    missing, digest = [], hashlib.sha256()
    answers, page_rows, promo_rows, queue = [], [], [], []
    cost = Decimal("0")
    providers = {model: Counter() for model in pair}
    counts = Counter()
    for page in pages:
        key = (page["catalog_id"], page["page"])
        found = _page_answers(page, out_dir, pair, registry)
        for model in pair:
            saved = found["saved"][model]
            if saved is None:
                missing.append(f"{model} {key[0]} p{key[1]}")
                continue
            if saved.get("image_sha256") != page["sha256"]:
                raise ExtractionError(f"answer for {key[0]} p{key[1]} is for another image")
            if saved.get("prompt_version") != PROMPT_VERSION:
                raise ExtractionError(f"answer for {key[0]} p{key[1]} used another prompt")
            path = result_path(out_dir, "", model, *key)
            digest.update(f"{path.relative_to(results_dir(out_dir))}\0".encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
            answer = saved["answer"]
            if answer.get("cost_usd") is not None:
                cost += Decimal(str(answer["cost_usd"]))
            providers[model][answer.get("provider") or "unknown"] += 1
            counts["over"] += int(bool(saved.get("over_price_ceiling")))
            parsed = found["parsed"][model]
            errors = parsed["item_errors"] if parsed else []
            counts["item_errors"] += len(errors)
            if parsed is None:
                counts[f"invalid_{'a' if model == a else 'b'}"] += 1
            if _invalid_dir(out_dir, model, page).exists():
                counts["retried"] += 1
            answers.append((ext_id, key[0], key[1], model, answer.get("provider"),
                            None if answer.get("cost_usd") is None else str(answer["cost_usd"]),
                            answer.get("prompt_tokens"), answer.get("completion_tokens"),
                            saved.get("prompt_sha256"), answer.get("content"),
                            found["error"][model], json.dumps(errors, ensure_ascii=False)))
        if missing:
            continue
        parsed = found["parsed"]
        ok = parsed[a] is not None and parsed[b] is not None
        page_rows.append((ext_id, key[0], key[1], page.get("dealer_id") or "",
                          page.get("chain") or "", page["sha256"],
                          int(bool(page.get("has_hotspots"))), page.get("valid_from") or "",
                          page.get("valid_to") or "",
                          parsed[a]["page_class"] if parsed[a] else None,
                          parsed[b]["page_class"] if parsed[b] else None,
                          "ok" if ok else "needs_review"))
        if not ok:
            queue.append({"catalog_id": key[0], "page": key[1], "reason": "no_valid_answer"})
        pairs, only_a, only_b, claims = _disputes(found["normalized"], pair)
        lines = ocr_lookup(key) if ((pairs or only_a or only_b) and ocr_lookup) else None
        saved_checks = {}
        for verifier in pair:
            if ok and claims[verifier]:
                numbered = xcheck.claims_for(page, claims[verifier])
                try:
                    saved_checks[verifier] = xcheck.load(out_dir, verifier, page, numbered)
                except PromotionOutputError as exc:
                    raise ExtractionError(str(exc)) from None
                if saved_checks[verifier] is not None:
                    counts["xcheck_calls"] += 1
                    xc = saved_checks[verifier]["answer"]
                    if xc.get("cost_usd") is not None:
                        cost += Decimal(str(xc["cost_usd"]))
                    counts["over"] += int(bool(saved_checks[verifier].get("over_price_ceiling")))
        items = [(x, y, "standing" if "standing_id" in x else "accepted", [a, b], None)
                 for x, y in pairs]
        for model, others, verifier in ((a, only_a, b), (b, only_b, a)):
            for item in others:
                if "standing_id" in item:
                    items.append((item, None, "standing", [model], None))
                    continue
                outcome = xcheck.decide(saved_checks.get(verifier), item, lines)
                outcome["verifier"] = verifier
                status = ("crosscheck_accepted" if outcome["accepted"]
                          else "refuted" if outcome.get("refuted") else "needs_review")
                items.append((item, None, status, [model], outcome))
        # A members-only conflict settled by the cross-check: the other version goes.
        settled = [x for x, _, status, _, _ in items if status == "crosscheck_accepted"]
        final = []
        for item, twin, status, by, outcome in items:
            if status == "needs_review" and any(
                    _key(item)[:2] == _key(s)[:2] and item["member_only"] != s["member_only"]
                    for s in settled):
                status = "superseded"
            final.append((item, twin, status, by, outcome))
        states = (ocr_status([x for x, *_ in final], lines) if (final and lines is not None)
                  else [])
        for number, (item, twin, status, by, outcome) in enumerate(final, 1):
            counts[status] += 1
            if status == "needs_review":
                queue.append({"catalog_id": key[0], "page": key[1], "reason": "disagreement",
                              "models": by, "promotion": {k: item[k] for k in (
                                  "kind", "n", "m", "percent", "amount_nok", "member_only",
                                  "scope")}})
            promo_rows.append((
                ext_id, key[0], key[1], number, item["kind"], item["n"], item["m"],
                item["percent"],
                None if item["amount_nok"] is None else int(round(item["amount_nok"] * 100)),
                int(item["member_only"]), item["scope"], twin["scope"] if twin else None,
                status, json.dumps(by), states[number - 1] if states else None, METHOD,
                item.get("standing_id"),
                json.dumps(outcome, ensure_ascii=False) if outcome else None))
    if missing:
        raise ExtractionError(f"{len(missing)} answers missing (e.g. {missing[0]}); "
                              "run with --execute to complete the extraction")
    health = monitor(list(pair), out_dir, keys, ocr_lookup=ocr_lookup)
    extraction = {
        "extraction_id": ext_id, "run_id": run_id, "prompt_version": PROMPT_VERSION,
        "model_a": a, "model_b": b, "pages": len(pages),
        "invalid_pages_a": counts["invalid_a"], "invalid_pages_b": counts["invalid_b"],
        "item_errors": counts["item_errors"],
        "agreed_items": health["agreed_items"], "disagreeing_items": health["disagreeing_items"],
        "agreement_rate": health["agreement_rate"],
        "ocr_engine": getattr(ocr_lookup, "engine", None),
        "ocr_checked_agreed": health["ocr_checked_agreed"],
        "ocr_confirmed_agreed": health["ocr_confirmed_agreed"],
        "ocr_confirmed_share": health["ocr_confirmed_share"],
        "review_queue_size": len(queue), "cost_usd": str(cost),
        "providers": json.dumps({m: dict(sorted(c.items())) for m, c in providers.items()},
                                sort_keys=True),
        "over_price_calls": counts["over"], "results_sha256": digest.hexdigest(),
        "standing_items": counts["standing"], "crosscheck_calls": counts["xcheck_calls"],
        "crosscheck_accepted": counts["crosscheck_accepted"],
        "superseded_items": counts["superseded"], "refuted_items": counts["refuted"],
        "retried_answers": counts["retried"]}
    return {"extraction": extraction, "pages": page_rows, "answers": answers,
            "promotions": promo_rows, "review_queue": queue}


def import_extraction(built: dict, db_path: Path, schema_dir: Path) -> dict:
    """Write one extraction in ONE transaction; identical re-import is a no-op."""
    ext = built["extraction"]
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for name in SCHEMAS:
            db.executescript((Path(schema_dir) / name).read_text(encoding="utf-8"))
        with db:
            if db.execute("SELECT 1 FROM tjek_runs WHERE run_id=?",
                          (ext["run_id"],)).fetchone() is None:
                raise ExtractionError(f"run {ext['run_id']} is not in the offer database; "
                                      "import it first (scripts/import_tjek_week.py)")
            known = db.execute("SELECT results_sha256 FROM promo_extractions "
                               "WHERE extraction_id=?", (ext["extraction_id"],)).fetchone()
            if known:
                if known[0] != ext["results_sha256"]:
                    raise ExtractionError("extraction already imported from different answers")
                return {"extraction_id": ext["extraction_id"], "imported": False}
            columns = list(ext) + ["imported_at"]
            db.execute(f"INSERT INTO promo_extractions ({','.join(columns)}) "
                       f"VALUES ({','.join('?' * len(columns))})",
                       [*ext.values(), datetime.now(timezone.utc).isoformat()])
            db.executemany("INSERT INTO promo_pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           built["pages"])
            db.executemany("INSERT INTO promo_answers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           built["answers"])
            db.executemany("INSERT INTO page_promotions VALUES "
                           "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", built["promotions"])
    return {"extraction_id": ext["extraction_id"], "imported": True}


def score_final(built: dict, reference: dict) -> dict:
    """What the PIPELINE decided, against a reference (pages it covers only).

    auto_wrong is the number that matters most: items stored without any
    person looking that are not on the page as labelled.
    """
    rows = {}
    for row in built["promotions"]:
        rows.setdefault((row[1], row[2]), []).append(row)
    totals = Counter()
    for ref in reference["pages"]:
        required = [_key(p) for p in ref["promotions"] if not p.get("optional")]
        optional = [_key(p) for p in ref["promotions"] if p.get("optional")]
        for row in rows.get((ref["catalog_id"], ref["page"]), []):
            promo = {"kind": row[4], "n": row[5], "m": row[6], "percent": row[7],
                     "amount_nok": None if row[8] is None else row[8] / 100,
                     "member_only": bool(row[9])}
            key = _key(promo)
            auto = row[12] in AUTO_STATUSES
            if key in required:
                required.remove(key)
                totals["required_auto" if auto else "required_refuted"
                       if row[12] == "refuted" else "required_in_review"] += 1
            elif key in optional:
                optional.remove(key)
                totals["optional_auto" if auto else "optional_in_review"] += 1
            elif row[12] == "refuted":
                totals["wrong_refuted"] += 1
            elif row[12] != "superseded":
                totals["auto_wrong" if auto else "wrong_in_review"] += 1
        totals["required_missing"] += len(required)
    return dict(totals)


def report(built: dict) -> list[str]:
    """Markdown summary: monitoring metrics and per-chain promotion counts."""
    ext = built["extraction"]
    chains = {}
    pages_by_key = {(row[1], row[2]): row for row in built["pages"]}
    for row in built["pages"]:
        chains.setdefault(row[4], Counter())["pages"] += 1
        chains[row[4]]["pages_review"] += row[11] == "needs_review"
    for row in built["promotions"]:
        chain = pages_by_key[(row[1], row[2])][4]
        chains[chain][row[12]] += 1
    lines = [f"# Promotions {ext['run_id']}", "",
             f"- Models: {ext['model_a']} + {ext['model_b']} ({ext['prompt_version']})",
             f"- Pages: {ext['pages']}; invalid answers: {ext['invalid_pages_a']} / "
             f"{ext['invalid_pages_b']} (retried {ext['retried_answers']}); "
             f"item errors: {ext['item_errors']}",
             f"- Raw agreement rate: {ext['agreement_rate']} ({ext['agreed_items']} agreed, "
             f"{ext['disagreeing_items']} disagreeing)",
             f"- Standing benefits: {ext['standing_items']}; cross-checks: "
             f"{ext['crosscheck_calls']} calls, {ext['crosscheck_accepted']} accepted, "
             f"{ext['refuted_items']} refuted, "
             f"{ext['superseded_items']} other versions superseded",
             f"- OCR-confirmed agreed items: {ext['ocr_confirmed_agreed']}/"
             f"{ext['ocr_checked_agreed']} ({ext['ocr_engine'] or 'no OCR'})",
             f"- Review queue: {ext['review_queue_size']}",
             f"- Cost: {ext['cost_usd']} USD; providers {ext['providers']}; "
             f"over price ceiling: {ext['over_price_calls']}", "",
             "| chain | pages | pages to review | accepted | cross-checked | standing | "
             "refuted | to review |", "|---|---|---|---|---|---|---|---|"]
    for chain, c in sorted(chains.items()):
        lines.append(f"| {chain} | {c['pages']} | {c['pages_review']} | {c['accepted']} | "
                     f"{c['crosscheck_accepted']} | {c['standing']} | {c['refuted']} | "
                     f"{c['needs_review']} |")
    return lines


# --- owner review of the queue -------------------------------------------

def describe(promo: dict) -> str:
    """'3 for 2', '-20 %', '-10 kr', '20 % bonus' plus member flag."""
    if promo["kind"] == "n_for_m":
        text = f"{promo['n']} for {promo['m']}"
    elif promo["kind"] == "percent_off":
        text = f"-{promo['percent']:g} %"
    elif promo["kind"] == "amount_off":
        amount = promo.get("amount_nok")
        if amount is None:
            amount = promo["amount_ore"] / 100
        text = f"-{amount:g} kr"
    else:
        text = f"{promo['percent']:g} % bonus"
    return text + (" (members)" if promo["member_only"] else "")


def _connect(db_path: Path, schema_dir: Path) -> sqlite3.Connection:
    if not Path(db_path).is_file():
        raise ExtractionError("offer database not found")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    for name in SCHEMAS:
        db.executescript((Path(schema_dir) / name).read_text(encoding="utf-8"))
    return db


def review_items(db_path: Path, schema_dir: Path, week: str | None = None) -> tuple[str, list]:
    """Open review items of one week (default: latest), with both models' answers."""
    from nutrivalue.page_promotions import PromotionOutputError, validate_output
    with _connect(db_path, schema_dir) as db:
        if week is None:
            row = db.execute("SELECT MAX(week) FROM v_week_extraction").fetchone()
            week = row[0]
        if week is None:
            raise ExtractionError("no extraction has been imported yet")
        items = []
        for row in db.execute("""SELECT * FROM v_week_promotion_review WHERE week=?
                                 ORDER BY chain_name, catalog_id, page, item_no""", (week,)):
            item = dict(row)
            page = dict(db.execute("""SELECT p.*, e.run_id FROM promo_pages p
                                      JOIN promo_extractions e USING(extraction_id)
                                      WHERE p.extraction_id=? AND p.catalog_id=? AND p.page=?""",
                                   (row["extraction_id"], row["catalog_id"], row["page"])).fetchone())
            item["page_info"] = page
            if row["item_no"] is not None:
                item["promotion"] = dict(db.execute(
                    """SELECT * FROM page_promotions WHERE extraction_id=? AND catalog_id=?
                       AND page=? AND item_no=?""",
                    (row["extraction_id"], row["catalog_id"], row["page"],
                     row["item_no"])).fetchone())
            answers = {}
            for answer in db.execute("""SELECT model, content FROM promo_answers
                                        WHERE extraction_id=? AND catalog_id=? AND page=?
                                        ORDER BY model""",
                                     (row["extraction_id"], row["catalog_id"], row["page"])):
                try:
                    answers[answer["model"]] = validate_output(answer["content"] or "")
                except PromotionOutputError:
                    answers[answer["model"]] = None
            item["answers"] = answers
            items.append(item)
    return week, items


def record_review(db_path: Path, schema_dir: Path, item: dict, decision: str,
                  note: str | None = None) -> None:
    """Append one decision ('accept' / 'reject' for an item, 'page_checked' for a page)."""
    with _connect(db_path, schema_dir) as db:
        insert_review(db, item, decision, note, datetime.now(timezone.utc).isoformat())


def insert_review(db: sqlite3.Connection, item: dict, decision: str, note: str | None,
                  reviewed_at: str) -> int:
    """One append-only decision on an open connection; returns its review_id."""
    expected = ("page_checked",) if item["item_no"] is None else ("accept", "reject")
    if decision not in expected:
        raise ExtractionError(f"decision must be one of {', '.join(expected)}")
    cursor = db.execute("""INSERT INTO promo_reviews (extraction_id,catalog_id,page,item_no,
                           decision,note,reviewed_at) VALUES (?,?,?,?,?,?,?)""",
                        (item["extraction_id"], item["catalog_id"], item["page"],
                         item["item_no"], decision, (note or "").strip() or None, reviewed_at))
    return cursor.lastrowid


def page_image(weekly_root: Path, run_id: str, catalog_id: str, page: int,
               sha256: str) -> Path | None:
    """The saved page image if it is still present and unchanged, else None."""
    run_dir = Path(weekly_root) / run_id
    try:
        pages = run_pages(run_dir, "all")
    except (OSError, ValueError, KeyError):
        return None
    for candidate in pages:
        if (candidate["catalog_id"], candidate["page"]) == (catalog_id, page):
            path = Path(candidate["path"])
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
                return path
    return None
