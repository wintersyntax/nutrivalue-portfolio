#!/usr/bin/env python3
"""Read-only summary of 7 manually labelled W42 extra FØR OCR observations."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import sys

EXPECTED = {(2,2):10, (3,13):10, (4,7):10, (7,11):11,
            (15,1):28, (16,24):40, (16,28):41}


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit('Usage: verify_w42_layout_v2.py /path/to/prior-price-regions.json')
    rows = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise SystemExit('Expected JSON list')
    by_key = {}
    for r in rows:
        k = (r.get('page'), r.get('prior_ocr_line'))
        by_key.setdefault(k, []).append(r)
    correct = wrong = unresolved = 0
    for k, expected in EXPECTED.items():
        entries = by_key.get(k, [])
        if len(entries) != 1:
            print(f'p{k[0]:02d} line {k[1]}: missing/duplicated ({len(entries)})')
            wrong += 1
            continue
        rec = entries[0]
        nums = [c.get('offer_number') for c in rec.get('candidate_products', [])]
        if rec.get('status') == 'region_candidate_review_only' and nums == [expected]:
            mark = 'CORRECT REVIEW CANDIDATE'; correct += 1
        elif nums:
            mark = 'WRONG OR AMBIGUOUS'; wrong += 1
        else:
            mark = 'UNRESOLVED'; unresolved += 1
        print(f'p{k[0]:02d} line {k[1]}: {mark}, expected #{expected}, '
              f'found {nums}, status={rec.get("status")}')
    ranges = by_key.get((13,13), [])
    range_ok = (len(ranges) == 1 and
                ranges[0].get('status') == 'unresolved_prior_price_range')
    prices_ok = all(not r.get('old_price_verified') and
                    not r.get('budget_price_modified') for r in rows)
    print(f'CORRECT {correct}/7; WRONG {wrong}/7; UNRESOLVED {unresolved}/7')
    print('All FØR statuses:', dict(Counter(r.get('status') for r in rows)))
    print('Prior-price range remains unresolved:', range_ok)
    print('No previous-price verification/DB-price modification flags:', prices_ok)
    if not range_ok or not prices_ok or wrong:
        print('FAIL-CLOSED: review data requires investigation')
        return 1
    print('Read-only review checks passed; this does NOT verify old shelf prices.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
