"""TASK-041: local-only, deterministic stratified human review of saved Tjek titles.

Source headlines are displayed ONLY on the owner's interactive alternate-screen
terminal; aggregate plan/result is the only shareable output. No new HTTP or DB.
Labels are persisted ONLY by explicit opt-in to ignored data/private/, mode 0600.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from nutrivalue.offer_categories import CATEGORIES, classify_offer
from nutrivalue.providers.tjek_offline import (
    CHAINS, MAX_FILE_BYTES, TjekSnapshotError, parse_snapshot,
)

CHOICES = {
    "f": "fish", "m": "meat", "d": "dairy", "v": "produce",
    "g": "grain", "p": "pantry", "z": "frozen", "r": "prepared",
    "b": "drink", "n": "non_food", "c": "snacks", "w": "sweets",
    "u": "unknown",
}
REVIEW_STATES = ("matched", "needs_review", "unmapped")


class ReviewError(ValueError):
    """Input or safety constraint failed without exposing raw private data."""


@dataclass(frozen=True)
class ReviewCandidate:
    chain: str
    heading: str
    predicted_category: str | None
    predicted_status: str
    predicted_food_key: str | None
    # Stable selection token not persisted/displayed outside local memory.
    ordering: str


def _read_one(path: Path):
    try:
        with path.open("rb") as f:
            raw = f.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise ReviewError("saved private archive unavailable") from exc
    try:
        snapshot = parse_snapshot(raw, path.name)
    except (TjekSnapshotError, ValueError) as exc:
        raise ReviewError("saved private archive failed validation") from exc
    dealers = {row[2] for row in snapshot.catalogs}
    if len(dealers) != 1:
        raise ReviewError("each archive must contain one selected chain")
    return next(iter(dealers)), snapshot


def sample_archives(paths: list[Path], *, per_bucket: int = 2) -> tuple[list[ReviewCandidate], dict]:
    """Return fixed, repeatable chain × rule-status sample, counts-only report.

    Raw titles exist only in returned in-process candidates, never in report.
    Repeated same title in the same chain is reviewed once; no random sampling.
    """
    if type(per_bucket) is not int or not 1 <= per_bucket <= 5:
        raise ReviewError("per-bucket must be 1..5")
    if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= len(CHAINS):
        raise ReviewError("require one to nine saved single-chain inputs")
    selected_dealers = set()
    buckets = defaultdict(list)
    observed = Counter()
    seen_headings = set()
    for path in paths:
        if not isinstance(path, Path):
            raise ReviewError("saved input path must be a Path")
        dealer, snap = _read_one(path)
        if dealer in selected_dealers:
            raise ReviewError("duplicate source chain in review inputs")
        selected_dealers.add(dealer)
        chain = CHAINS[dealer]
        for offer in snap.offers:
            if offer[3] != dealer:
                raise ReviewError("source offer has inconsistent chain")
            # Normalized headline key for same-chain duplicate titles; do not
            # publish IDs or hashes as they can re-identify a private offer.
            title = offer[4]
            normalized = " ".join(title.casefold().split())
            key = (chain, normalized)
            if key in seen_headings:
                continue
            seen_headings.add(key)
            decision = classify_offer(title, offer[5])
            observed[(chain, decision.status)] += 1
            token = hashlib.sha256(f"task041|{chain}|{normalized}".encode()).hexdigest()
            buckets[(chain, decision.status)].append(ReviewCandidate(
                chain, title, decision.food_category, decision.status,
                decision.food_key, token))
    chosen = []
    for (chain, status), candidates in sorted(buckets.items()):
        chosen.extend(sorted(candidates, key=lambda x: x.ordering)[:per_bucket])
    chosen.sort(key=lambda x: (x.chain, REVIEW_STATES.index(x.predicted_status),
                                x.ordering))
    per_chain = {}
    for chain in sorted(set(CHAINS[dealer] for dealer in selected_dealers)):
        per_chain[chain] = {
            status: {"available_distinct_titles": observed[(chain, status)],
                     "sampled": sum(x.chain == chain and x.predicted_status == status
                                    for x in chosen)}
            for status in REVIEW_STATES
        }
    return chosen, {
        "method": "sha256_ordered_stratified_chain_x_status",
        "sample_per_chain_status": per_bucket,
        "input_chains": len(selected_dealers),
        "missing_selected_chains": sorted(set(CHAINS.values()) - set(per_chain)),
        "sample_size": len(chosen),
        "by_chain_and_status": per_chain,
        "network_requests": 0,
        "database_writes": 0,
        "raw_private_text_in_report": False,
    }


def make_label(candidate: ReviewCandidate, category: str) -> dict:
    if category not in set(CHOICES.values()):
        raise ReviewError("unknown human review category")
    # Retain raw title *only* inside locally written opt-in private JSONL.
    return {
        "heading": candidate.heading, "chain": candidate.chain,
        "label": category, "rule_category": candidate.predicted_category,
        "rule_status": candidate.predicted_status,
    }


def private_labels_write(target: Path, labels: list[dict], *,
                         private_root: Path) -> None:
    """One-shot exclusive 0600 write under private root; no unsafe overwrite."""
    if not labels:
        raise ReviewError("no owner-reviewed labels to save")
    root = private_root.resolve(strict=True)
    if not root.is_dir() or not target.name.endswith(".jsonl"):
        raise ReviewError("label destination must be private .jsonl")
    output = target.resolve(strict=False)
    if root not in output.parents or target.is_symlink():
        raise ReviewError("label destination must be within ignored data/private")
    if not output.parent.is_dir():
        raise ReviewError("private label directory is missing")
    if output.exists():
        raise ReviewError("private label file already exists; choose a fresh name")
    for record in labels:
        if not isinstance(record, dict) or not isinstance(record.get("heading"), str) \
                or record.get("label") not in CHOICES.values():
            raise ReviewError("invalid human review record")
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                      for row in labels).encode("utf-8")
    # Write only after an explicit --review --labels CLI invocation. Owner
    # may intentionally keep local labeled examples for a future model.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def aggregate_review(labels: list[dict], *, sampled: int) -> dict:
    """Safe-to-paste counts only; no source titles/offer IDs/prices."""
    per_label = Counter(row["label"] for row in labels)
    rule_agrees = sum((row["rule_category"] or "unknown") == row["label"]
                      for row in labels)
    return {
        "sampled": sampled, "reviewed": len(labels),
        "not_reviewed": sampled - len(labels),
        "owner_label_counts": dict(sorted(per_label.items())),
        "rule_category_agreements": rule_agrees,
        "rule_category_disagreements": len(labels) - rule_agrees,
        "no_raw_source_titles_or_prices": True,
        "network_requests": 0,
        "database_writes": 0,
    }


def interactive_review(candidates: list[ReviewCandidate]) -> list[dict]:
    """curses alternate screen: private source titles never enter shell log."""
    import curses

    def screen(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        result = []
        legend_parts = [f"{k}={v}" for k, v in CHOICES.items()]
        for position, item in enumerate(candidates, 1):
            while True:
                stdscr.erase()
                rows, cols = stdscr.getmaxyx()
                if rows < 12 or cols < 45:
                    raise ReviewError("terminal too small for private review")
                def add(y: int, value: str):
                    stdscr.addnstr(y, 0, value, cols - 1)
                add(0, f"PRIVATE LOCAL REVIEW {position}/{len(candidates)} — not for sharing")
                add(1, f"CHAIN: {item.chain} | RULE: {item.predicted_status}")
                add(2, f"RULE CATEGORY: {item.predicted_category or 'none'}")
                add(3, f"TYPE SUGGESTION: {item.predicted_food_key or 'none'} (NOT SKU)")
                visible = "".join(" " if ord(c) < 32 or ord(c) == 127 else c
                                  for c in item.heading)
                add(5, f"PRIVATE HEADING: {visible[:cols - 20]}")
                lines = [""]
                for part in legend_parts:
                    if len(lines[-1]) + len(part) + 1 > cols - 1:
                        lines.append(part)
                    else:
                        lines[-1] += (" " if lines[-1] else "") + part
                if 6 + len(lines) + 2 >= rows:
                    raise ReviewError("terminal too small for full category legend")
                for index, legend_line in enumerate(lines):
                    add(6 + index, legend_line)
                add(6 + len(lines), "a=accept rule category  s=skip  q=finish  ?=unsure")
                stdscr.refresh()
                try:
                    key = stdscr.getkey().lower()
                except KeyboardInterrupt:
                    return result
                if key == "q":
                    return result
                if key in ("s", "?"):
                    break
                if key == "a" and item.predicted_category is not None:
                    result.append(make_label(item, item.predicted_category))
                    break
                if key in CHOICES:
                    result.append(make_label(item, CHOICES[key]))
                    break
        return result

    try:
        return curses.wrapper(screen)
    except curses.error as exc:
        raise ReviewError("interactive terminal review is unavailable") from exc
