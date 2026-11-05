"""Offline GS1 GTIN syntax and check-digit validation for identity candidates.

A valid GTIN is only a syntactically valid identifier. It does not establish
product equivalence, manufacturer evidence, nutrition, or a verified price.
Unknown productCode/SKU/id fields must never be treated as GTIN assertions.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


LENGTHS = frozenset((8, 12, 13, 14))
# Explicitly typed EAN/UPC/GTIN or generic barcode observations only.
# Generic "id", "sku" and "productCode" are deliberately excluded.
KIND_LENGTHS = {
    "gtin": None,
    "gtin8": 8,
    "gtin12": 12,
    "gtin13": 13,
    "gtin14": 14,
    "ean": None,
    "ean8": 8,
    "ean13": 13,
    "upc": 12,
    "upca": 12,
    "barcode": None,
}


def canonical_gtin14(value: object) -> str | None:
    """Validate ASCII GTIN-8/12/13/14 and return 14-digit zero-padded form.

    No trimming, separator removal, numeric coercion, or guessing of lost zeros.
    Invalid inputs return None and must not be used as an exact identity join.
    """
    if type(value) is not str or len(value) not in LENGTHS:
        return None
    if re.fullmatch(r"[0-9]+", value) is None:
        return None
    body = value[:-1]
    checksum = sum(
        int(digit) * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(body))
    )
    expected = (10 - checksum % 10) % 10
    if expected != int(value[-1]):
        return None
    return value.zfill(14)


@dataclass(frozen=True)
class GTINAssertion:
    """Untrusted identifier assertion with explicit type and source provenance."""

    value: object
    kind: str
    source: str
    source_path: str


@dataclass(frozen=True)
class GTINCandidate:
    assertion: GTINAssertion
    canonical_gtin14: str


@dataclass(frozen=True)
class GTINAssessment:
    # "candidate_only" never means that a cross-source SKU match was verified.
    status: str  # none, candidate_only, or conflict
    candidates: tuple[GTINCandidate, ...]
    rejected: tuple[GTINAssertion, ...]
    distinct_gtin14: tuple[str, ...]


def assess_gtin_assertions(assertions: list[GTINAssertion] | tuple[GTINAssertion, ...]) -> GTINAssessment:
    """Retain all assertions; never silently pick an identifier on conflict.

    Only explicitly labeled GTIN/EAN/UPC/barcode fields may be assessed.
    A checksum-valid number in an internal SKU/id field is still rejected.
    """
    if not isinstance(assertions, (list, tuple)):
        raise TypeError("GTIN assertions must be a list or tuple")
    candidates: list[GTINCandidate] = []
    rejected: list[GTINAssertion] = []
    for assertion in assertions:
        if not isinstance(assertion, GTINAssertion):
            raise TypeError("Expected GTINAssertion")
        if (not isinstance(assertion.source, str) or not assertion.source.strip()
                or not isinstance(assertion.source_path, str)
                or not assertion.source_path.strip()):
            raise ValueError("Each assertion requires source and source_path")
        kind = (assertion.kind.casefold().replace("_", "").replace("-", "")
                if isinstance(assertion.kind, str) else "")
        if kind not in KIND_LENGTHS:
            rejected.append(assertion)
            continue
        expected_length = KIND_LENGTHS[kind]
        value = assertion.value
        if expected_length is not None and (type(value) is not str or len(value) != expected_length):
            rejected.append(assertion)
            continue
        canonical = canonical_gtin14(value)
        if canonical is None:
            rejected.append(assertion)
        else:
            candidates.append(GTINCandidate(assertion, canonical))
    distinct = tuple(sorted({entry.canonical_gtin14 for entry in candidates}))
    status = ("none" if not distinct else
              "candidate_only" if len(distinct) == 1 else
              "conflict")
    return GTINAssessment(status, tuple(candidates), tuple(rejected), distinct)
