#!/usr/bin/env python3
"""Score a completely reviewed W41 candidate and omission list. No OCR/API calls."""
import argparse, json
from pathlib import Path
from w41_full_catalog import score_review, read_csv, CANDIDATE_FIELDS, PAGE_FIELDS, MISS_FIELDS

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out-dir',type=Path,required=True)
    a=p.parse_args()
    o=a.out_dir
    d=json.loads((o/'candidates.json').read_text())
    if d.get('schema')!='nutrivalue_w41_full_catalog_candidates_v1' or d.get('week')!='2026-W41':p.error('Wrong W41 catalog schema')
    r=score_review(d,read_csv(o/'review-candidates.csv',CANDIDATE_FIELDS),read_csv(o/'page-completion.csv',PAGE_FIELDS),read_csv(o/'missed-ads.csv',MISS_FIELDS))
    (o/'score.json').write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(r,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
