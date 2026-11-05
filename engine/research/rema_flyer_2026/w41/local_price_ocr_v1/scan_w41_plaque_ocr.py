#!/usr/bin/env python3
"""Offline, read-only price OCR inside V2 blue plaques from REMA W41.

This is deliberately separate from the frozen V2 offer matcher. It never
constructs products/SKUs, verifies historical prices, or writes to a DB.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import cv2
import numpy as np

PRICE_PART = re.compile(r"\d{1,3}")
CENTS_PART = re.compile(r"\d{2}")


def sha256(path: Path) -> str:
    out=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(2**20),b''):
            out.update(block)
    return out.hexdigest()


def is_private_dir(path: Path) -> bool:
    parts=path.resolve().parts
    return any(parts[i:i+3]==('data','private','tjek') for i in range(len(parts)-2))


def plaque_crop(image, b):
    h,w=image.shape[:2]
    x0=round(float(b['x0'])*w); x1=round(float(b['x1'])*w)
    top=round((1-float(b['y1']))*h); bottom=round((1-float(b['y0']))*h)
    if not (0<=x0<x1<=w and 0<=top<bottom<=h):
        raise ValueError('Invalid blue plaque bounds')
    return image[top:bottom,x0:x1]


def subregion(img, relative):
    h,w=img.shape[:2]
    a,b,c,d=relative
    x0=round(w*a);x1=round(w*b); y0=round(h*c);y1=round(h*d)
    if not (0<=x0<x1<=w and 0<=y0<y1<=h):
        raise ValueError('Invalid subregion')
    return img[y0:y1,x0:x1]


def prepare(img, method, scale):
    if method=='gray':
        gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    elif method=='white':
        hsv=cv2.cvtColor(img,cv2.COLOR_BGR2HSV)
        gray=cv2.inRange(hsv,(0,0,140),(179,100,255))
    else:
        raise ValueError('Unknown OCR preprocessing')
    resized=cv2.resize(gray,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(resized,32,32,32,32,cv2.BORDER_CONSTANT,value=255)


def tesseract_digits(prepared, *, psm=13):
    ok,data=cv2.imencode('.png',prepared)
    if not ok:raise ValueError('Failed to encode OCR crop')
    cmd=['tesseract','stdin','stdout','-l','eng','--psm',str(psm),
         '-c','tessedit_char_whitelist=0123456789']
    run=subprocess.run(cmd,input=data.tobytes(),stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE,timeout=20,check=False)
    if run.returncode:
        raise RuntimeError('Local Tesseract error: '+run.stderr.decode(errors='replace')[:300])
    return re.sub(r'\s+','',run.stdout.decode('utf-8',errors='replace'))


# Independent OCR views of the left large numeral and right superscript cents.
# No catalog-price lookup, no OCR correction dictionary, no hidden price defaults.
VIEWS={
    'major':[
        ('major_gray_psm10',(.04,.62,.08,.87),'gray',5,10),
        ('major_white_psm10',(.04,.62,.08,.87),'white',5,10),
        ('major_gray_psm13',(.04,.62,.08,.87),'gray',5,13),
    ],
    'minor':[
        ('minor_gray_psm13',(.61,.98,.10,.59),'gray',6,13),
        ('minor_white_psm13',(.61,.98,.10,.59),'white',6,13),
        ('minor_gray_shift_psm13',(.65,.98,.10,.59),'gray',6,13),
    ],
}


def consensus(reads, expr):
    valid=[r['raw'] for r in reads if expr.fullmatch(r['raw'])]
    counts=Counter(valid)
    if not counts:return None
    value,votes=counts.most_common(1)[0]
    # Reject ties and lone recognitions. Variants are correlated, so even a
    # consensus is only review-only OCR, not independently verified evidence.
    if votes<2 or sum(n>=votes for n in counts.values())>1:return None
    return value


def inspect_plaque(roi):
    evidence={}
    for kind,views in VIEWS.items():
        evidence[kind]=[]
        for name,region,method,scale,psm in views:
            raw=tesseract_digits(prepare(subregion(roi,region),method,scale),psm=psm)
            evidence[kind].append({'view':name,'raw':raw})
            # When two distinct OCR views agree on a complete price part,
            # avoid unnecessary additional Tesseract invocations.
            pattern = PRICE_PART if kind=='major' else CENTS_PART
            if len(evidence[kind])>=2 and consensus(evidence[kind],pattern) is not None:
                break
    major=consensus(evidence['major'],PRICE_PART)
    minor=consensus(evidence['minor'],CENTS_PART)
    if major is None or minor is None:
        status='unresolved_digits_not_consistent_review_only'
        amount=None
    else:
        amount=Decimal(int(major))+Decimal(int(minor))/100
        if not Decimal('1.00')<=amount<=Decimal('999.99'):
            amount=None; status='unresolved_out_of_price_range_review_only'
        else:
            status='plaque_split_digits_candidate_review_only'
    return {'status':status,'current_price_nok_review_only':f'{amount:.2f}' if amount is not None else None,
            'major_votes':dict(Counter(r['raw'] for r in evidence['major'] if PRICE_PART.fullmatch(r['raw']))),
            'minor_votes':dict(Counter(r['raw'] for r in evidence['minor'] if CENTS_PART.fullmatch(r['raw']))),
            'ocr_views':evidence}


def bbox_diff(b1,b2):
    return max(abs(float(b1[k])-float(b2[k])) for k in ('x0','x1','y0','y1'))


def scan_pages(src: Path, images: Path, geometry: dict, scope: str):
    expected_hash=geometry.get('source_parser_sha256')
    observed_hash=sha256(src/'ad_layout_regions.py')
    if not expected_hash or expected_hash!=observed_hash:
        raise ValueError('V2 parser SHA mismatch: use the same source as baseline geometry; STOP')
    sys.path.insert(0,str(src.resolve()))
    import ad_layout_regions as al
    if not hasattr(al,'_blue_price_badge_boxes'):
        raise ValueError('V2 plaque detector missing')
    pages=[]
    for original in geometry['pages']:
        number=int(original['page'])
        path=images/f'page-{number:02d}.jpg'
        image=cv2.imread(str(path))
        if image is None:raise ValueError('Missing or unreadable page: '+str(path))
        badges=list(al._blue_price_badge_boxes(str(path)))
        if len(badges)!=int(original['blue_plaque_count']):
            raise ValueError(f'V2 plaque count drift on page {number}')
        observations=[]
        for prior in original['observations']:
            p=prior.get('blue_plaque_box')
            if p is None:continue
            aligned=[i for i,b in enumerate(badges) if bbox_diff(p,b)<.003]
            if len(aligned)!=1:
                raise ValueError(f'V2 prior/plaque alignment drift on page {number}')
            idx=aligned[0]
            current=inspect_plaque(plaque_crop(image,badges[idx]))
            amount=current['current_price_nok_review_only']
            if amount is not None and Decimal(amount)>=Decimal(prior['ocr_prior_nok']):
                current['status']='review_price_not_below_prior'
            current.update({'page':number,'blue_plaque_index':idx,
                            'prior_ocr_line':prior['line_index'],
                            'prior_ocr_nok':prior['ocr_prior_nok'],
                            'prior_text':prior['prior_text'],
                            'blue_plaque_box':badges[idx],
                            'verified_product_identity':False,
                            'verified_historical_shelf_price':False,'database_writes':False})
            observations.append(current)
        scanned_idx={o['blue_plaque_index'] for o in observations}
        if scope=='all':
            for idx,b in enumerate(badges):
                if idx in scanned_idx:continue
                current=inspect_plaque(plaque_crop(image,b))
                current.update({'page':number,'blue_plaque_index':idx,
                                'prior_ocr_line':None,'prior_ocr_nok':None,
                                'prior_text':None,'blue_plaque_box':b,
                                'verified_product_identity':False,
                                'verified_historical_shelf_price':False,'database_writes':False})
                observations.append(current)
        pages.append({'page':number,'blue_plaque_count':len(badges),
                      'observations':observations,'image_sha256':sha256(path)})
        if number%5==0 or number==29:
            print(f'Processed {number}/29 pages...',flush=True)
    return pages


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--src',type=Path,required=True)
    ap.add_argument('--image-dir',type=Path,required=True)
    ap.add_argument('--geometry-json',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    ap.add_argument('--scope',choices=['prior','all'],default='prior')
    args=ap.parse_args(argv)
    if not is_private_dir(args.out_dir) or args.out_dir.exists() or args.out_dir.is_symlink():
        ap.error('Output must be NEW folder inside data/private/tjek/')
    if shutil.which('tesseract') is None:ap.error('Install local Tesseract executable first')
    geometry=json.loads(args.geometry_json.read_text(encoding='utf-8'))
    if geometry.get('schema')!='nutrivalue_w41_independent_plaque_geometry_v1' or geometry.get('page_count')!=29:
        ap.error('Expected independent W41 geometry v1 of 29 pages')
    if len(geometry.get('pages',[]))!=29:
        ap.error('Incomplete baseline geometry pages')
    if not args.src.is_dir() or not args.image_dir.is_dir():ap.error('Missing local parser/images')
    result_pages=scan_pages(args.src,args.image_dir,geometry,args.scope)
    observations=[x for p in result_pages for x in p['observations']]
    aligned=[x for x in observations if x['prior_ocr_line'] is not None]
    counts=Counter(x['status'] for x in observations)
    result={'schema':'nutrivalue_w41_local_plaque_ocr_v1',
            'week':'2026-W41','scope':args.scope,'pages_scanned':29,
            'source_v2_parser_sha256':geometry['source_parser_sha256'],
            'baseline_blue_plaques':sum(p['blue_plaque_count'] for p in geometry['pages']),
            'baseline_explicit_priors':geometry['ocr_prior_count'],
            'tesseract_version':subprocess.run(['tesseract','--version'],capture_output=True,text=True).stdout.splitlines()[0],
            'candidate_count':sum(x['current_price_nok_review_only'] is not None for x in observations),
            'prior_aligned_candidate_count':sum(x['status']=='plaque_split_digits_candidate_review_only' for x in aligned),
            'status_counts':dict(sorted(counts.items())),'pages':result_pages,
            'verified_shelf_prices':0,'verified_product_identities':0,
            'database_writes':False,'provider_calls':0,
            'limitations':['OCR variants are correlated and may agree on incorrect digits.',
                           'Blue plaques may be false positives: visual reference still required.',
                           'This does not establish offer matching, SKU identity or historic price correctness.',
                           'W41 page 01 Kjeldsberg overlaps with known W42 promotion.']}
    args.out_dir.mkdir(parents=True)
    (args.out_dir/'plaque-price-ocr-w41.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# W41 local plaque price OCR (review-only)','',
           f'Pages: 29 | V2 blue plaques: {result["baseline_blue_plaques"]} | scope: {args.scope}',
           f'Previous OCR: {geometry["ocr_prior_count"]} explicit FØR, 0 current-price candidates in their plaques',
           f'After crop OCR: {result["prior_aligned_candidate_count"]} / {len(aligned)} aligned FØR current-price candidates',
           f'All plaque OCR candidates scanned: {result["candidate_count"]} / {len(observations)}',
           '', '## Aligned prior / current OCR candidates']
    for o in aligned:
        lines.append(f'- p{o["page"]:02d} FØR {o["prior_ocr_nok"]} -> '+
                     f'{o["current_price_nok_review_only"] or "?"} NOK | {o["status"]} | '+
                     f'major={o["major_votes"]} cents={o["minor_votes"]}')
    if args.scope=='all':
        lines+=['','## Every blue-plaque OCR proposal (NOT all necessarily true prices)']
        for page in result_pages:
            for o in page['observations']:
                lines.append(f'- p{page["page"]:02d} badge {o["blue_plaque_index"]}: '
                             f'{o["current_price_nok_review_only"] or "?"} NOK '
                             f'| {o["status"]}')
    lines+=['','## Status counts']+[f'- {k}: {v}' for k,v in sorted(counts.items())]
    lines+=['','No HTTP, provider OCR, Tjek matching, historical-price verification, or database writes.','Source-image visual review required before operational use.']
    (args.out_dir/'audit.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'W41 local plaque OCR: {len(observations)} plaques evaluated, '
          f'{result["candidate_count"]} OCR candidates; prior-aligned '
          f'{result["prior_aligned_candidate_count"]}/{len(aligned)} candidates')
    print('NO HTTP | NO CLOUD OCR | NO TJEK IDENTITY | NO DB WRITES')
    print('Report:',args.out_dir/'audit.md')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
