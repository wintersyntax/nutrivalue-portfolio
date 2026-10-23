"""TASK-053/056: owner-run, budget-capped comparison of vision models on pages.

Every model receives the same prompt, schema and page bytes. Paid calls
happen only with ``execute=True``; each one is reserved in a durable budget
ledger BEFORE the request and settled from OpenRouter's reported cost after
it. Unknown or uncertain cost blocks further calls. Existing results are
never re-requested, so an interrupted run resumes without double spending.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import time
from decimal import Decimal
from pathlib import Path

from nutrivalue.page_promotions import (
    OUTPUT_SCHEMA, PROMPT_VERSION, PromotionOutputError, build_prompt, ocr_lines, ocr_status,
    score_page, validate_output,
)
from nutrivalue.providers.openrouter import price_ceiling_usd, provider_policy


EST_PROMPT_TOKENS = 6000
MAX_OUTPUT_TOKENS = 3000
SAFETY = Decimal("1.5")
MIN_RESERVATION = Decimal("0.01")
# HTTP statuses where the provider rejected the request before generation.
REJECTED_STATUSES = frozenset({400, 401, 402, 403, 404, 408, 413, 422, 429})
CALL_INTERVAL_S = 2.0
RATE_LIMIT_WAIT_S = 30.0
MAX_RATE_LIMIT_WAITS = 10


class BenchmarkError(RuntimeError):
    """The benchmark must stop without spending more."""


LABEL = re.compile(r"[A-Za-z0-9_-]{1,32}")


def _slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


def results_dir(out_dir: Path, label: str = "") -> Path:
    """Unlabelled results keep the TASK-053 layout; labelled repeats get their own tree."""
    if not label:
        return Path(out_dir) / "results"
    if not LABEL.fullmatch(label):
        raise BenchmarkError("run label must be 1-32 letters, digits, '-' or '_'")
    return Path(out_dir) / "runs" / label / "results"


def result_path(out_dir: Path, label: str, model: str, catalog_id: str, page: int) -> Path:
    return results_dir(out_dir, label) / _slug(model) / f"{catalog_id}_p{page:03d}.json"


def load_answer(out_dir: Path, label: str, model: str, catalog_id: str, page: int):
    """(saved, parsed, error) re-validated with the CURRENT contract (no network)."""
    path = result_path(out_dir, label, model, catalog_id, page)
    if not path.exists():
        return None, None, None
    saved = json.loads(path.read_text())
    try:
        return saved, validate_output(saved["answer"].get("content") or ""), None
    except PromotionOutputError as exc:
        return saved, None, str(exc)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, path)


class Budget:
    def __init__(self, path: Path, cap_usd: Decimal):
        self.path = Path(path)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if Decimal(self.data["cap_usd"]) != cap_usd:
                raise BenchmarkError("budget cap differs from the existing ledger")
        else:
            self.data = {"cap_usd": str(cap_usd), "attempts": []}
            _write(self.path, self.data)

    def committed(self) -> Decimal:
        total = Decimal("0")
        for a in self.data["attempts"]:
            if a["status"] == "settled":
                total += Decimal(a["actual_usd"])
            elif a["status"] not in ("rejected", "released_by_owner"):
                total += Decimal(a["reserved_usd"])
        return total

    def blocked(self) -> bool:
        return any(a["status"] in ("reserved", "uncertain", "unknown_cost")
                   for a in self.data["attempts"])

    def reserve(self, key: str, amount: Decimal) -> int:
        if self.blocked():
            raise BenchmarkError("an earlier call has uncertain or unknown cost; stop")
        if self.committed() + amount > Decimal(self.data["cap_usd"]):
            raise BenchmarkError("budget cap would be exceeded; stop")
        self.data["attempts"].append({"key": key, "status": "reserved",
                                      "reserved_usd": str(amount), "actual_usd": None})
        _write(self.path, self.data)
        return len(self.data["attempts"]) - 1

    def settle(self, index: int, cost) -> None:
        attempt = self.data["attempts"][index]
        if cost is None:
            attempt["status"] = "unknown_cost"
        else:
            attempt["status"] = "settled"
            attempt["actual_usd"] = str(Decimal(str(cost)))
        _write(self.path, self.data)

    def uncertain(self, index: int) -> None:
        self.data["attempts"][index]["status"] = "uncertain"
        _write(self.path, self.data)

    def rejected(self, index: int, http_status: int) -> None:
        """Provider refused the request before generation: nothing to settle."""
        attempt = self.data["attempts"][index]
        attempt["status"] = "rejected"
        attempt["http_status"] = http_status
        _write(self.path, self.data)

    def release(self, key: str, reason: str) -> None:
        """Explicit owner decision for ONE uncertain attempt, recorded with reason."""
        matches = [a for a in self.data["attempts"]
                   if a["key"] == key and a["status"] in ("uncertain", "reserved")]
        if len(matches) != 1 or not reason.strip():
            raise BenchmarkError("release needs exactly one uncertain attempt and a reason")
        matches[0]["status"] = "released_by_owner"
        matches[0]["release_reason"] = reason.strip()
        _write(self.path, self.data)


def reservation(info: dict) -> Decimal:
    estimate = (Decimal(str(info["prompt_usd_per_token"])) * EST_PROMPT_TOKENS
                + Decimal(str(info["completion_usd_per_token"])) * MAX_OUTPUT_TOKENS) * SAFETY
    return max(estimate.quantize(Decimal("0.000001")), MIN_RESERVATION)


def ocr_lines_for(image: Path, cache: Path) -> tuple[str, list[str]] | None:
    """Local OCR lines for evidence checks: RapidOCR (with boxes) when
    installed, otherwise Tesseract text; None when neither is available.

    Measured on the W43 reference: RapidOCR + layout joining confirmed 70/71
    true badge values; Tesseract (three PSM modes) found 52/71 numbers.
    """
    if cache.is_file():
        data = json.loads(cache.read_text())
        return data["engine"], data["lines"]
    engine, lines = None, None
    try:
        from rapidocr import RapidOCR  # optional dependency
    except ImportError:
        RapidOCR = None
    if RapidOCR is not None:
        result = _rapid(RapidOCR)(str(image))
        tokens = list(zip(result.txts or (), result.boxes if result.boxes is not None else ()))
        engine, lines = "rapidocr", ocr_lines([(t, [list(c) for c in b]) for t, b in tokens])
    else:
        exe = shutil.which("tesseract")
        if exe is None:
            return None
        listed = subprocess.run([exe, "--list-langs"], capture_output=True, text=True).stdout
        lang = "nor+eng" if "nor" in listed.split() else "eng"
        parts = []
        for psm in ("11", "3", "6"):
            done = subprocess.run([exe, str(image), "stdout", "-l", lang, "--psm", psm],
                                  capture_output=True, text=True, timeout=120)
            if done.returncode != 0:
                return None
            parts.append(done.stdout)
        engine = "tesseract"
        lines = [line.casefold() for line in "\n".join(parts).splitlines() if line.strip()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"engine": engine, "lines": lines}, ensure_ascii=False))
    return engine, lines


_RAPID = {}


def _rapid(factory):
    if "engine" not in _RAPID:
        _RAPID["engine"] = factory()
    return _RAPID["engine"]


def run(pages: list[dict], models: list[str], *, out_dir: Path, cap_usd: Decimal,
        catalogue: dict, client, execute: bool, interval_s: float = CALL_INTERVAL_S,
        sleep=time.sleep, rate_limit_wait_s: float = RATE_LIMIT_WAIT_S,
        max_rate_limit_waits: int = MAX_RATE_LIMIT_WAITS, label: str = "") -> dict:
    """Call each model once per page (unless already saved). Returns a call log.

    Each page gets the catalogue context (chain, label, validity) in its prompt.
    A run ``label`` stores an independent repeat next to earlier results.
    """
    out_dir = Path(out_dir)
    missing = [m for m in models if m not in catalogue]
    if missing:
        raise BenchmarkError(f"models not available on OpenRouter: {', '.join(missing)}")
    for model in models:
        if "image" not in catalogue[model]["input_modalities"]:
            raise BenchmarkError(f"{model} does not accept images")
    plan = []
    for page in pages:          # page-major: models alternate, no burst per provider
        for model in models:
            plan.append((model, page, result_path(out_dir, label, model, page["catalog_id"],
                                                  page["page"])))
    todo = [item for item in plan if not item[2].exists()]
    estimate = sum((reservation(catalogue[m]) for m, _, _ in todo), Decimal("0"))
    log = {"planned_calls": len(plan), "remaining_calls": len(todo),
           "max_reserved_estimate_usd": str(estimate), "executed_calls": 0,
           "rate_limit_waits": 0}
    if not execute:
        return log
    budget = Budget(out_dir / "budget.json", cap_usd)
    for model, page, target in todo:
        info = catalogue[model]
        prompt = build_prompt(page)
        image = Path(page["path"]).read_bytes()
        if hashlib.sha256(image).hexdigest() != page["sha256"]:
            raise BenchmarkError(f"page image hash mismatch: {page['catalog_id']} p{page['page']}")
        key = f"{model}|{page['catalog_id']}|{page['page']}" + (f"|{label}" if label else "")
        call = paid_call(budget, key, model, info, client, prompt, image, OUTPUT_SCHEMA, log,
                         sleep=sleep, interval_s=interval_s, rate_limit_wait_s=rate_limit_wait_s,
                         max_rate_limit_waits=max_rate_limit_waits)
        answer = call["answer"]
        try:
            parsed, error = validate_output(answer["content"] or ""), None
        except PromotionOutputError as exc:
            parsed, error = None, str(exc)
        _write(target, {"model": model, "catalog_id": page["catalog_id"], "page": page["page"],
                        "image_sha256": page["sha256"], "label": label,
                        "prompt_version": PROMPT_VERSION,
                        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                        "provider_policy": call["policy"],
                        "price_ceiling_usd": call["ceiling"],
                        "over_price_ceiling": call["over"],
                        "answer": answer, "parsed": parsed, "validation_error": error})
        after_call(model, call)
    return log


def paid_call(budget: Budget, key: str, model: str, info: dict, client, prompt: str,
              image: bytes, schema: dict, log: dict, *, sleep=time.sleep,
              interval_s: float = CALL_INTERVAL_S, rate_limit_wait_s: float = RATE_LIMIT_WAIT_S,
              max_rate_limit_waits: int = MAX_RATE_LIMIT_WAITS) -> dict:
    """One budget-reserved, routed call; settles the actual cost.

    Rejected requests (4xx incl. 429) are recorded as such; a 429 waits a
    bounded number of times. Server/network errors and interrupts leave the
    reservation 'uncertain', which blocks further spending until the owner
    releases it. The caller saves the answer, then calls ``after_call``.
    """
    policy = provider_policy(model, info)
    while True:
        if log["executed_calls"] or log["rate_limit_waits"]:
            sleep(interval_s)
        index = budget.reserve(key, reservation(info))
        try:
            answer = client.complete(
                model, prompt, image, schema,
                structured="structured_outputs" in info["supported_parameters"],
                temperature="temperature" in info["supported_parameters"],
                max_tokens=MAX_OUTPUT_TOKENS, provider=policy)
            break
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status in REJECTED_STATUSES:
                budget.rejected(index, status)
                # A 429 is refused before generation (not billed): wait a
                # bounded number of times, then stop for a later rerun.
                if status == 429 and log["rate_limit_waits"] < max_rate_limit_waits:
                    log["rate_limit_waits"] += 1
                    sleep(rate_limit_wait_s)
                    continue
                hint = " (rate limited: wait and rerun)" if status == 429 else ""
                raise BenchmarkError(f"{model}: {exc}{hint}") from None
            budget.uncertain(index)   # server/network error: charge unknown -> block
            raise BenchmarkError(f"{model}: {exc}; stopped without retry") from None
        except BaseException:
            budget.uncertain(index)   # Ctrl-C / exit mid-call: outcome unknown
            raise
    budget.settle(index, answer.get("cost_usd"))
    ceiling = price_ceiling_usd(policy, answer.get("prompt_tokens"),
                                answer.get("completion_tokens"))
    over = (ceiling is not None and answer.get("cost_usd") is not None
            and answer["cost_usd"] > ceiling * 1.02 + 1e-7)
    log["executed_calls"] += 1
    return {"answer": answer, "policy": policy, "ceiling": ceiling, "over": over}


def after_call(model: str, call: dict) -> None:
    """Stop spending after a saved answer with unknown or excessive cost."""
    answer = call["answer"]
    if answer.get("cost_usd") is None:
        raise BenchmarkError(f"{model} reported no cost; stopped")
    if call["over"]:
        raise BenchmarkError(f"{model}: charged {answer['cost_usd']} USD above the price "
                             f"ceiling {call['ceiling']:.7f} via {answer.get('provider')}; "
                             "stopped")


def score(reference: dict, models: list[str], out_dir: Path, *, ocr_dir: Path | None = None,
          pages: list[dict] | None = None, label: str = "") -> dict:
    """Aggregate per-model metrics from saved results (no network).

    Saved answers are re-validated with the current contract, so a contract
    change (TASK-056 item-level validation) can be re-scored without new calls.
    """
    out_dir = Path(out_dir)
    by_key = {(p["catalog_id"], p["page"]): p for p in reference["pages"]}
    paths = {(p["catalog_id"], p["page"]): p["path"] for p in pages or []}
    report = {}
    for model in models:
        totals = {"pages": 0, "answered": 0, "invalid": 0, "item_errors": 0,
                  "page_class_correct": 0, "tp": 0, "fp": 0, "fn": 0, "member_errors": 0,
                  "ocr_confirmed": 0, "ocr_number_only": 0, "ocr_absent": 0,
                  "ocr_engine": None, "cost_usd": Decimal("0"), "prompt_versions": set(),
                  "providers": {}, "over_price": 0,
                  "latencies": [], "scope_overlaps": []}
        for key, ref in sorted(by_key.items()):
            totals["pages"] += 1
            saved, parsed, _ = load_answer(out_dir, label, model, *key)
            if saved:
                totals["answered"] += 1
                totals["prompt_versions"].add(saved.get("prompt_version") or "page_promotions@1")
                host = saved["answer"].get("provider") or "unknown"
                totals["providers"][host] = totals["providers"].get(host, 0) + 1
                totals["over_price"] += int(bool(saved.get("over_price_ceiling")))
                if saved["answer"].get("cost_usd") is not None:
                    totals["cost_usd"] += Decimal(str(saved["answer"]["cost_usd"]))
                totals["latencies"].append(saved["answer"].get("latency_s") or 0)
                if parsed is None:
                    totals["invalid"] += 1
                else:
                    totals["item_errors"] += len(parsed["item_errors"])
            result = score_page(ref, parsed)
            for field in ("tp", "fp", "fn", "member_errors"):
                totals[field] += result[field]
            totals["page_class_correct"] += int(result["page_class_correct"])
            totals["scope_overlaps"] += result["scope_overlaps"]
            if parsed and parsed["promotions"] and ocr_dir is not None and key in paths:
                found = ocr_lines_for(Path(paths[key]),
                                      Path(ocr_dir) / f"{key[0]}_p{key[1]:03d}.json")
                if found is not None:
                    totals["ocr_engine"] = found[0]
                    for state in ocr_status(parsed["promotions"], found[1]):
                        totals[f"ocr_{state}"] += 1
        tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
        report[model] = {
            "pages": totals["pages"], "answered": totals["answered"],
            "invalid_json": totals["invalid"], "item_errors": totals["item_errors"],
            "prompt_versions": sorted(totals["prompt_versions"]),
            "providers": dict(sorted(totals["providers"].items())),
            "over_price_calls": totals["over_price"],
            "page_class_accuracy": round(totals["page_class_correct"] / totals["pages"], 3)
            if totals["pages"] else None,
            "promotion_precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "promotion_recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "true_positives": tp, "false_assertions": fp, "missed": fn,
            "member_flag_errors": totals["member_errors"],
            "ocr_engine": totals["ocr_engine"],
            "ocr_confirmed": totals["ocr_confirmed"],
            "ocr_number_only": totals["ocr_number_only"],
            "ocr_absent": totals["ocr_absent"],
            "median_scope_overlap": round(statistics.median(totals["scope_overlaps"]), 3)
            if totals["scope_overlaps"] else None,
            "cost_usd": str(totals["cost_usd"]),
            "median_latency_s": round(statistics.median(totals["latencies"]), 2)
            if totals["latencies"] else None,
        }
    return report


def rank(report: dict) -> list[str]:
    """Fewest false assertions first, then recall, then cost."""
    return sorted(report, key=lambda m: (report[m]["false_assertions"] + report[m]["invalid_json"],
                                         -(report[m]["promotion_recall"] or 0),
                                         Decimal(report[m]["cost_usd"])))


def _key(p: dict) -> tuple:
    if p["kind"] == "n_for_m":
        value = (p["n"], p["m"])
    elif p["kind"] == "amount_off":
        value = (round(float(p["amount_nok"]), 2),)
    else:
        value = (round(float(p["percent"]), 2),)
    return (p["kind"], value, p["member_only"])


def _keyed(promotions: list[dict]) -> list[tuple]:
    return [_key(p) for p in promotions]


def pair_items(pa: dict | None, pb: dict | None):
    """([(item_a, item_b)], only_a, only_b) on one page.

    Items pair on kind, numbers and member flag; scope is free text and is
    only used to choose among several candidates with the same value (e.g.
    three different "3 for 2" groups). Such an ambiguous pair whose scopes
    share no word is NOT agreement: both items go to review.
    """
    from nutrivalue.page_promotions import scope_overlap
    items_a = list(pa["promotions"]) if pa else []
    rest_b = list(pb["promotions"]) if pb else []
    agreed, only_a = [], []
    for item in items_a:
        same = [other for other in rest_b if _key(other) == _key(item)]
        if not same:
            only_a.append(item)
            continue
        twin = max(same, key=lambda other: scope_overlap(other["scope"], item["scope"]))
        ambiguous = len(same) > 1 or sum(_key(x) == _key(item) for x in items_a) > 1
        if ambiguous and scope_overlap(twin["scope"], item["scope"]) == 0:
            only_a.append(item)
            continue
        rest_b.remove(twin)
        agreed.append((item, twin))
    return agreed, only_a, rest_b


def _agree(pa: dict | None, pb: dict | None) -> tuple[list[dict], list[dict], list[dict]]:
    """(agreed, only_a, only_b) - see pair_items."""
    pairs, only_a, only_b = pair_items(pa, pb)
    return [a for a, _ in pairs], only_a, only_b


def _ocr_lookup(pages: list[dict] | None, ocr_dir: Path | None):
    paths = {(p["catalog_id"], p["page"]): p["path"] for p in pages or []}

    def lookup(key):
        if ocr_dir is None or key not in paths:
            return None
        found = ocr_lines_for(Path(paths[key]), Path(ocr_dir) / f"{key[0]}_p{key[1]:03d}.json")
        return found[1] if found else None
    return lookup


def monitor(pair: list[str], out_dir: Path, page_keys: list[tuple], *, label: str = "",
            ocr_lookup=None) -> dict:
    """Production health of a two-model setup WITHOUT a reference.

    agreement_rate = agreed items / (agreed + disagreeing items);
    ocr_confirmed_share = agreed items whose badge OCR confirms / agreed items
    that had OCR lines; review_queue = everything a person must look at
    (disagreeing items and pages a model did not answer validly).
    """
    if len(pair) != 2 or pair[0] == pair[1]:
        raise BenchmarkError("monitor needs exactly two different models")
    a, b = pair
    stats = {m: {"answered": 0, "invalid_pages": 0, "item_errors": 0} for m in pair}
    agreed_n = disagree_n = checked = confirmed = 0
    queue = []
    for key in page_keys:
        parsed = {}
        for model in pair:
            saved, parsed[model], _ = load_answer(out_dir, label, model, *key)
            if saved:
                stats[model]["answered"] += 1
                if parsed[model] is None:
                    stats[model]["invalid_pages"] += 1
                else:
                    stats[model]["item_errors"] += len(parsed[model]["item_errors"])
        missing = [m for m in pair if parsed[m] is None]
        if missing:
            queue.append({"catalog_id": key[0], "page": key[1], "reason": "no_valid_answer",
                          "models": missing})
        agreed, only_a, only_b = _agree(parsed[a], parsed[b])
        agreed_n += len(agreed)
        disagree_n += len(only_a) + len(only_b)
        for model, items in ((a, only_a), (b, only_b)):
            for item in items:
                queue.append({"catalog_id": key[0], "page": key[1], "reason": "disagreement",
                              "models": [model], "promotion": item})
        lines = ocr_lookup(key) if (agreed and ocr_lookup) else None
        if lines is not None:
            states = ocr_status(agreed, lines)
            checked += len(states)
            confirmed += states.count("confirmed")
    total = agreed_n + disagree_n
    return {"pair": list(pair), "label": label, "pages": len(page_keys), "models": stats,
            "agreed_items": agreed_n, "disagreeing_items": disagree_n,
            "agreement_rate": round(agreed_n / total, 3) if total else None,
            "ocr_checked_agreed": checked, "ocr_confirmed_agreed": confirmed,
            "ocr_confirmed_share": round(confirmed / checked, 3) if checked else None,
            "review_queue_size": len(queue), "review_queue": queue}


def pairwise(reference: dict, models: list[str], out_dir: Path, *, label: str = "",
             ocr_lookup=None) -> list[dict]:
    """Two independent models: what they agree on, and how often that is right.

    Agreement = same kind, numbers and member flag on the same page (scope is
    not compared). Disagreements are what a two-model setup sends to review.
    """
    out_dir = Path(out_dir)
    keys = [(ref["catalog_id"], ref["page"]) for ref in reference["pages"]]
    answers = {(model, *key): load_answer(out_dir, label, model, *key)[1]
               for model in models for key in keys}
    required_total = sum(1 for ref in reference["pages"]
                         for p in ref["promotions"] if not p.get("optional"))
    rows = []
    for i, a in enumerate(models):
        for b in models[i + 1:]:
            agreed = correct = required_hit = review = 0
            for ref in reference["pages"]:
                key = (ref["catalog_id"], ref["page"])
                both, only_a, only_b = _agree(answers[(a, *key)], answers[(b, *key)])
                truth = _keyed([p for p in ref["promotions"] if not p.get("optional")])
                optional = _keyed([p for p in ref["promotions"] if p.get("optional")])
                for item in _keyed(both):
                    agreed += 1
                    if item in truth:
                        truth.remove(item)
                        correct += 1
                        required_hit += 1
                    elif item in optional:
                        optional.remove(item)
                        correct += 1
                review += len(only_a) + len(only_b)
            health = monitor([a, b], out_dir, keys, label=label, ocr_lookup=ocr_lookup)
            rows.append({"pair": [a, b], "agreed": agreed, "agreed_correct": correct,
                         "agreed_wrong": agreed - correct,
                         "agreed_recall": round(required_hit / required_total, 3)
                         if required_total else None,
                         "disagreements_for_review": review,
                         "agreement_rate": health["agreement_rate"],
                         "ocr_confirmed_share": health["ocr_confirmed_share"],
                         "review_queue_size": health["review_queue_size"]})
    # Useful agreement minus a heavy penalty for agreed errors (those pass
    # unreviewed); pairs that agree on nothing must not rank first.
    return sorted(rows, key=lambda r: (-(r["agreed_correct"] - 5 * r["agreed_wrong"]),
                                       r["disagreements_for_review"]))


def consistency(models: list[str], out_dir: Path, labels: list[str],
                page_keys: list[tuple]) -> dict:
    """Run-to-run stability of each model between two labelled repeats (no reference)."""
    if len(labels) != 2 or labels[0] == labels[1]:
        raise BenchmarkError("consistency needs two different run labels")
    first, second = labels
    out = {}
    for model in models:
        row = {"compared_pages": 0, "identical_pages": 0, "page_class_changes": 0,
               "items_only_first": 0, "items_only_second": 0, "unanswered_or_invalid": 0,
               "changed_pages": []}
        for key in page_keys:
            pa = load_answer(out_dir, first, model, *key)[1]
            pb = load_answer(out_dir, second, model, *key)[1]
            if pa is None or pb is None:
                row["unanswered_or_invalid"] += 1
                continue
            row["compared_pages"] += 1
            _, only_a, only_b = _agree(pa, pb)
            row["items_only_first"] += len(only_a)
            row["items_only_second"] += len(only_b)
            class_changed = pa["page_class"] != pb["page_class"]
            row["page_class_changes"] += int(class_changed)
            if only_a or only_b or class_changed:
                row["changed_pages"].append({"catalog_id": key[0], "page": key[1]})
            else:
                row["identical_pages"] += 1
        row["stability"] = (round(row["identical_pages"] / row["compared_pages"], 3)
                            if row["compared_pages"] else None)
        out[model] = row
    return {"labels": list(labels), "models": out}
