"""TASK-041 experimental local character n-gram Naive Bayes category baseline.

Trains in memory on owner-confirmed PRIVATE labels only. Evaluates against a
title-group-held-out split versus deterministic rules. NEVER auto-tags offers
or saves weights; does not download models or use third-party packages.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from nutrivalue.offer_categories import classify_offer
from nutrivalue.offer_review import CHOICES, ReviewError

MAX_LABEL_BYTES = 1_000_000
MAX_LABELS = 3000


def _text_norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _ngrams(text: str) -> Counter:
    letters = f" {_text_norm(text)} "
    counts = Counter()
    for length in (2, 3, 4, 5):
        counts.update(letters[i:i + length]
                      for i in range(max(0, len(letters) - length + 1)))
    return counts


def load_private_labels(path: Path, *, private_root: Path) -> list[dict]:
    """Read only explicitly owner-reviewed private JSONL, fail closed on leaks."""
    try:
        root = private_root.resolve(strict=False)
        destination = path.resolve(strict=True)
    except OSError as exc:
        raise ReviewError("owner-private label file is unavailable") from exc
    if root not in destination.parents or path.is_symlink():
        raise ReviewError("label file must remain inside ignored data/private")
    try:
        with destination.open("rb") as f:
            raw = f.read(MAX_LABEL_BYTES + 1)
    except OSError as exc:
        raise ReviewError("cannot read owner-private label file") from exc
    if len(raw) > MAX_LABEL_BYTES:
        raise ReviewError("private labeled input exceeds bounded size")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ReviewError("invalid private label encoding") from exc
    if not 1 <= len(lines) <= MAX_LABELS:
        raise ReviewError("invalid private label count")
    records = []
    key_to_label = {}
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewError("invalid private JSONL label file") from exc
        if (not isinstance(row, dict)
                or not isinstance(row.get("heading"), str)
                or not 1 <= len(row["heading"]) <= 400
                or row.get("label") not in CHOICES.values()
                or not isinstance(row.get("chain"), str)):
            raise ReviewError("invalid human-reviewed private label")
        key = _text_norm(row["heading"])
        previous = key_to_label.get(key)
        if previous is not None and previous != row["label"]:
            raise ReviewError("contradictory owner labels for same normalized title")
        key_to_label[key] = row["label"]
        records.append({"heading": row["heading"], "label": row["label"]})
    return records


class NgramNB:
    """Dependency-free experimental bag-of-character-ngrams classifier."""

    def __init__(self, examples: list[dict]):
        self.class_docs = Counter()
        self.feature_counts = defaultdict(Counter)
        self.feature_total = Counter()
        vocab = set()
        for row in examples:
            category = row["label"]
            features = _ngrams(row["heading"])
            self.class_docs[category] += 1
            self.feature_counts[category].update(features)
            self.feature_total[category] += sum(features.values())
            vocab.update(features)
        if not self.class_docs:
            raise ReviewError("no training labels")
        self.vocab_size = max(1, len(vocab))
        self.total_docs = sum(self.class_docs.values())

    def predict(self, heading: str) -> str:
        features = _ngrams(heading)
        best_label, best_score = None, -math.inf
        for label in sorted(self.class_docs):
            total = self.feature_total[label] + self.vocab_size
            score = math.log(self.class_docs[label] / self.total_docs)
            counts = self.feature_counts[label]
            for item, occurrence in features.items():
                score += occurrence * (
                    math.log(counts[item] + 1) - math.log(total))
            if score > best_score:
                best_label, best_score = label, score
        assert best_label is not None
        return best_label


def evaluate_labels(examples: list[dict]) -> dict:
    """Group exact-normalized titles; never train and test same title."""
    per_title = {}
    for row in examples:
        title = _text_norm(row["heading"])
        current = per_title.get(title)
        if current is not None and current["label"] != row["label"]:
            raise ReviewError("conflicting labels for repeated private heading")
        per_title[title] = row
    labels = defaultdict(list)
    for row in per_title.values():
        labels[row["label"]].append(row)
    train, holdout, not_testable = [], [], []
    for label, items in sorted(labels.items()):
        ranked = sorted(items, key=lambda row: hashlib.sha256(
            ("TASK041-SPLIT|" + _text_norm(row["heading"])).encode()).hexdigest())
        if len(ranked) < 4:
            train.extend(ranked)
            not_testable.append(label)
            continue
        validation_size = max(1, len(ranked) // 4)
        holdout.extend(ranked[:validation_size])
        train.extend(ranked[validation_size:])
    if len(set(row["label"] for row in train)) < 2 or len(holdout) < 2:
        raise ReviewError("need >=2 classes and >=2 held-out labeled titles")
    model = NgramNB(train)
    rules_ok = 0
    ml_ok = 0
    rule_abstains = 0
    rule_model_disagreements = 0
    total = len(holdout)
    recall = defaultdict(lambda: Counter({"total": 0, "rule_correct": 0, "ml_correct": 0}))
    for row in holdout:
        human = row["label"]
        rule = classify_offer(row["heading"])
        rule_cat = rule.food_category or "unknown"
        predicted = model.predict(row["heading"])
        rules_ok += (rule_cat == human)
        ml_ok += (predicted == human)
        rule_abstains += rule.food_category is None
        rule_model_disagreements += (rule_cat != predicted)
        recall[human]["total"] += 1
        recall[human]["rule_correct"] += rule_cat == human
        recall[human]["ml_correct"] += predicted == human
    return {
        "method": "local_character_ngrams_2_to_5_multinomial_naive_bayes",
        "deployment_state": "EXPERIMENT_ONLY_NOT_USED_FOR_AUTO_TAGGING",
        "validation": "deterministic_disjoint_normalized_heading_groups",
        "unique_owner_labeled_headings": len(per_title),
        "train_groups": len(train),
        "held_out_groups": total,
        "categories_without_holdout": not_testable,
        "rule_correct": rules_ok,
        "ml_correct": ml_ok,
        "rule_abstained": rule_abstains,
        "rule_vs_ml_disagreements": rule_model_disagreements,
        "by_true_category": {key: dict(value) for key, value in sorted(recall.items())},
        "note": ("Small, deliberately sampled owner labels; no calibrated confidence, "
                 "no generalization guarantee, no automatic source approval."),
        "raw_titles_in_report": False,
        "network_requests": 0,
        "file_writes": 0,
    }
