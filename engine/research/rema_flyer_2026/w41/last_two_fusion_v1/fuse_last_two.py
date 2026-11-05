#!/usr/bin/env python3
"""Offline W41: fuse paired Paddle crop glyphs into review-only price proposals.

No ground-truth reference values used. Never writes to production DB or source data.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

WEEK = "2026-W41"
SCHEMA = "nutrivalue_w41_last_two_paddle_review_only_v1"
TARGETS = {
    "p02-couscous": {
        "candidate_id": "W41-P02-N001", "page": 2, "title_fragment": "COUSCOUS",
        "price": ("p02-w41-p02-n001-price-review.png", "4f6a98a81c988cfa2078db78b1bbe875346f5ab343e338023a55d72be99b7198"),
        "advert": ("p02-w41-p02-n001-advert-review.png", "c36870db742114623fcc3df7d3d9d1e83df108416b66f6074dbbe4e09afbb3ac"),
    },
    "p16-egg6pk": {
        "candidate_id": "W41-P16-N001", "page": 16, "title_fragment": "EGG6PK",
        "price": ("p16-w41-p16-n001-price-review.png", "69ebca190e62096a6855569a7863114560f08231a27a2ed8e7be88d119d11d07"),
        "advert": ("p16-w41-p16-n001-advert-review.png", "4b2a30e2702da762c289ae0cdb6593c083e3280eb0db91a65b0f0e806a0db75a"),
    },
}


def filehash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def verify_source(v3):
    if v3.get('week') != WEEK or v3.get('pages') != 29 or not v3.get('review_only'):
        raise ValueError('Invalid W41 V3 dataset/week/pages/review_only')
    records=v3.get('candidates')
    if not isinstance(records,list) or len(records)!=104:
        raise ValueError('Expect exactly 104 candidates in W41 V3')
    ids=[c.get('candidate_id') for c in records]
    if len(ids)!=len(set(ids)):
        raise ValueError('Duplicate candidate IDs')
    for issue,t in TARGETS.items():
        candidates=[c for c in records if c.get('candidate_id')==t['candidate_id']]
        if len(candidates)!=1:
            raise ValueError(f'Missing/duplicated target {issue}')
        c=candidates[0]
        if c.get('page')!=t['page'] or c.get('candidate_source')!='vision_partial_price_fragment':
            raise ValueError(f'Unexpected target origin: {issue}')
        if c.get('printed_current_nok') is not None or c.get('pricing_mode')!='PRICE_UNREADABLE_REVIEW_ONLY':
            raise ValueError(f'Target already has proposed current price: {issue}')
        if c.get('publishable') or c.get('tjek_identity_verified'):
            raise ValueError(f'Unsafe publishable or Tjek-verified status: {issue}')
        title=str(c.get('guarded_product_title_v3','')).upper().replace(' ','').replace('-','')
        if t['title_fragment'] not in title:
            raise ValueError(f'Title mismatch: {issue}: {title}')
        initial=str(c.get('source_evidence',{}).get('read_major_only',''))
        if not re.fullmatch(r'\d{1,3}',initial):
            raise ValueError(f'No Vision major-only evidence: {issue}')
    return {c['candidate_id']:c for c in records}


def verify_paddle(p):
    if p.get('schema')!=SCHEMA or p.get('week')!=WEEK or not p.get('review_only') or p.get('image_count')!=4:
        raise ValueError('Invalid Paddle results file')
    crops=p.get('crops')
    if not isinstance(crops,list) or len(crops)!=4:
        raise ValueError('Expected four Paddle crops')
    seen={}
    for crop in crops:
        issue,mode=crop.get('issue'),crop.get('mode')
        if issue not in TARGETS or mode not in ('price','advert') or (issue,mode) in seen:
            raise ValueError('Unknown/duplicate Paddle crop')
        filename, expected_sha=TARGETS[issue][mode]
        if crop.get('file')!=filename or crop.get('image_sha256')!=expected_sha:
            raise ValueError(f'Image provenance failed: {issue}/{mode}')
        if not isinstance(crop.get('lines'),list):
            raise ValueError('Missing Paddle lines')
        seen[issue,mode]=crop
    if len(seen)!=4:
        raise ValueError('Some crops are missing')
    return seen


def rect(line):
    vals=line.get('box')
    if not isinstance(vals,list) or len(vals)!=4 or any(not isinstance(x,(int,float)) for x in vals):
        return None
    x1,y1,x2,y2=map(float,vals)
    if x1<0 or y1<0 or x2<=x1 or y2<=y1:
        return None
    return x1,y1,x2,y2


def price_in_crop(lines, vision_major):
    majors=[]; cents=[]
    for ln in lines:
        box=rect(ln)
        if box is None or not isinstance(ln.get('text'),str):
            continue
        conf=ln.get('confidence')
        if not isinstance(conf,(float,int)) or conf < 0.95:
            continue
        value=ln['text'].strip()
        if value==vision_major:
            majors.append((ln,box))
        if re.fullmatch(r'\d{2}',value):
            cents.append((ln,box))
    possibles=[]
    for major, mb in majors:
        mx1,my1,mx2,my2=mb; mw=mx2-mx1; mh=my2-my1
        for cent, cb in cents:
            if cent is major:continue
            cx1,cy1,cx2,cy2=cb
            yoverlap=min(my2,cy2)-max(my1,cy1)
            rightof=(cx1>=mx1 + 0.6*mw) and (cx1 <= mx2+0.8*mw)
            yaligned=yoverlap>0 and abs((cy1+cy2)/2-(my1+my2)/2)<0.7*mh
            smaller=(cy2-cy1)<0.9*mh
            if rightof and yaligned and smaller:
                money=f'{int(vision_major)}.{int(cent["text"]):02d}'
                possibles.append((money,major,cent))
    dedup={(v,tuple(ma['box']),tuple(ce['box'])):(v,ma,ce) for v,ma,ce in possibles}
    if len(dedup)!=1:
        raise ValueError(f'Ambiguous/unreadable split price: {vision_major}, candidates={len(dedup)}')
    return next(iter(dedup.values()))


def assert_title_in_crop(lines, phrase):
    strings=[re.sub('[^A-Z0-9]','',str(l.get('text','')).upper()) for l in lines]
    if not any(phrase in s for s in strings):
        raise ValueError(f'Missing OCR product caption: {phrase}')


def fuse(v3, paddle):
    original=verify_source(v3)
    cropmap=verify_paddle(paddle)
    output=deepcopy(v3)
    output['schema']='nutrivalue_w41_full_catalog_last_two_fusion_v4_review_only'
    output['parent_schema']=v3.get('schema')
    output['review_only']=True
    output['tjek_identity_verified']=False
    output['manual_full_catalog_reference_complete']=False
    output['full_catalog_recall']=None
    output['correct_product_price_recall']=None
    new={c['candidate_id']:c for c in output['candidates']}
    proposals=[]
    for issue,t in TARGETS.items():
        cid=t['candidate_id']; c=new[cid]; major=str(original[cid]['source_evidence']['read_major_only'])
        evidence=[]
        for mode in ('price','advert'):
            crop=cropmap[issue,mode]
            if mode=='advert':assert_title_in_crop(crop['lines'],t['title_fragment'])
            amount,whole,cents=price_in_crop(crop['lines'],major)
            evidence.append({'mode':mode,'image':crop['file'],'image_sha256':crop['image_sha256'],
                             'full_amount_candidate_nok':amount,
                             'major':{'text':whole['text'],'box':whole['box'],'confidence':whole['confidence']},
                             'ore':{'text':cents['text'],'box':cents['box'],'confidence':cents['confidence']}})
        amounts={e['full_amount_candidate_nok'] for e in evidence}
        if len(amounts)!=1:raise ValueError(f'Crops disagree for {issue}')
        amount=amounts.pop()
        # Preserve original W41 V3 values and provenance; only add a review-only proposal.
        c['targeted_paddle_price_candidate_nok_v4']=amount
        c['targeted_paddle_price_evidence_v4']=evidence
        c['targeted_paddle_price_status_v4']='SAME_MODEL_TWO_CROPS_SPLIT_PRICE_REVIEW_ONLY'
        c['price_accepted_v4']=False
        c['review_only']=True
        c['publishable']=False
        proposals.append({'candidate_id':cid,'page':t['page'],'proposed_title':c['guarded_product_title_v3'],
                          'paddle_price_candidate_nok':amount,'status':c['targeted_paddle_price_status_v4']})
    output['last_two_paddle_price_proposals_v4']=proposals
    assert len(output['candidates'])==len(v3['candidates'])==104
    return output,proposals


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--v3',type=Path,required=True)
    ap.add_argument('--paddle',type=Path,required=True,help='results.json from last-two PaddleOCR')
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args()
    if not a.v3.is_file() or not a.paddle.is_file():ap.error('Missing input JSON')
    if a.out_dir.exists():ap.error('Output directory already exists')
    v3=json.loads(a.v3.read_text(encoding='utf-8'))
    p=json.loads(a.paddle.read_text(encoding='utf-8'))
    fused,proposals=fuse(v3,p)
    a.out_dir.mkdir(parents=True)
    (a.out_dir/'candidates-v4.json').write_text(json.dumps(fused,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (a.out_dir/'audit.md').write_text(
        '# W41 — Targeted Paddle evidence fusion V4 (REVIEW ONLY)\n\n'
        'Input V3 candidates: 104; retained 104; proposed missing prices: 2.\n\n'
        +''.join(f'- {x["candidate_id"]}: {x["proposed_title"]} → {x["paddle_price_candidate_nok"]} NOK; {x["status"]}\n' for x in proposals)
        +'\nNo price is accepted or published. Same Paddle model on two crops is not independent model agreement.\n'
        'Original printed_current_nok remains null for both entries.\n'
        'Full-catalog product/price recall is STILL NOT MEASURED. No W41 Tjek IDs, network, OCR, LLM or DB writes.\n',
        encoding='utf-8')
    with (a.out_dir/'price-proposals.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(proposals[0]));w.writeheader();w.writerows(proposals)
    print((a.out_dir/'audit.md').read_text(encoding='utf-8'))

if __name__=='__main__':main()
