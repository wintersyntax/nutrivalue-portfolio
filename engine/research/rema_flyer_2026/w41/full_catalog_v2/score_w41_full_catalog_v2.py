#!/usr/bin/env python3
"""Quality gate for *independently reviewed* W41 V2 results; no inferred scores."""
import argparse
import json
from pathlib import Path
from w41_full_catalog import score_review,read_csv,CANDIDATE_FIELDS,PAGE_FIELDS,MISS_FIELDS

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out-dir',type=Path,required=True)
    a=p.parse_args(argv)
    out=a.out_dir
    d=json.loads((out/'candidates.json').read_text(encoding='utf-8'))
    if d.get('schema')!='nutrivalue_w41_full_catalog_candidates_v2_review_only' or d.get('week')!='2026-W41':
        p.error('Incorrect source week/schema')
    result=score_review(d,read_csv(out/'review-candidates.csv',CANDIDATE_FIELDS),
        read_csv(out/'page-completion.csv',PAGE_FIELDS),read_csv(out/'missed-ads.csv',MISS_FIELDS))
    (out/'score.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
