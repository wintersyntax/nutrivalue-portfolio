#!/usr/bin/env python3
"""Offline W41 Vision/Tesseract evidence comparison on independent visual price labels.

No Vision API/network calls, no Tjek identities, no LLM, no DB writes.
A price read is a review candidate even if independent OCR engines agree.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import re
import unicodedata

DECIMAL = re.compile(r'(?<!\d)(\d{1,3})\s*[,\.]\s*(\d{2})(?!\d)')
SPLIT = re.compile(r'^\s*(\d{1,3})\s+(\d{2})(?:\s+PR\.?\s*(?:KG|STK))?\s*$', re.I)
NUMBER = re.compile(r'^\s*\d{1,3}\s*$')
# OCR sometimes merges large major digits and small superscript cents (e.g. 10690).
# Only parse these as money with a STRICT full-string AND visual price-badge shape gate.
COMPACT_BADGE_PRICE = re.compile(r'^([1-9]\d{1,2})(\d{2})$')
PRIOR = re.compile(r'\bF[ØO0]R\b', re.I)


def private_path(path: Path) -> bool:
    parts = path.resolve().parts
    return any(parts[i:i+2] == ('data', 'private') for i in range(len(parts)-1))


def price(whole: str, cents: str) -> str:
    return f'{int(whole)}.{int(cents):02d}'


def boxes_intersect(a: dict, b: dict) -> bool:
    for key in ('x','y','width','height'):
        if not isinstance(a.get(key), (int,float)):
            return False
    return min(a['x']+a['width'],b['x1'])>max(a['x'],b['x0']) and min(a['y']+a['height'],b['y1'])>max(a['y'],b['y0'])


def normalize_line(line: dict) -> dict | None:
    if not isinstance(line.get('text'),str) or not isinstance(line.get('box'),dict):return None
    b=line['box']; text=line['text'].strip()
    if not text or not all(isinstance(b.get(k),(int,float)) for k in ('x','y','width','height')):return None
    if not (0<=b['x']<=1 and 0<=b['y']<=1 and b['width']>0 and b['height']>0):return None
    return {'text':text,'box':b,'confidence':line.get('confidence')}


def dominant(values: list[str]) -> str | None:
    return next(iter(set(values))) if len(set(values))==1 and values else None


def vision_plaque_price(lines: list[dict], plaque: dict) -> dict:
    """Explicit decimal text or geometrically separated major+øre only.

    Conservative: multiple distinct readings => ambiguous; no catalog price guessing.
    """
    selected=[]
    for entry in lines:
        ln=normalize_line(entry)
        if ln and boxes_intersect(ln['box'],plaque):
            xc=ln['box']['x']+ln['box']['width']/2
            yc=ln['box']['y']+ln['box']['height']/2
            rx=(xc-plaque['x0'])/(plaque['x1']-plaque['x0'])
            ry=(yc-plaque['y0'])/(plaque['y1']-plaque['y0'])
            selected.append((ln,rx,ry))
    prices=[]; majors=[];cents=[]
    for ln,rx,ry in selected:
        txt=unicodedata.normalize('NFKC',ln['text']).strip()
        # Reject OCR text whose bounding box extends far beyond the plaque.
        bbox=ln['box']
        inside=(bbox['x']>=plaque['x0']-.012 and bbox['x']+bbox['width']<=plaque['x1']+.012
                and bbox['y']>=plaque['y0']-.012 and bbox['y']+bbox['height']<=plaque['y1']+.012)
        if not inside:continue
        for a,b in DECIMAL.findall(txt):
            prices.append(price(a,b))
        m=SPLIT.match(txt)
        if m and 0<=rx<=1:
            prices.append(price(m.group(1),m.group(2)))
        # Google Vision often recognizes a printed 106^90 as one string '10690'.
        # NEVER interpret arbitrary catalog digits as prices: standalone four/five
        # digits, spatially inside the large blue plaque, with price-sized glyphs.
        compact=COMPACT_BADGE_PRICE.fullmatch(txt)
        plaque_w=plaque['x1']-plaque['x0']
        plaque_h=plaque['y1']-plaque['y0']
        if (compact and plaque_w>0 and plaque_h>0 and len(txt) in (4,5)
                and .40<=bbox['width']/plaque_w<=1.05
                and .25<=bbox['height']/plaque_h<=.85
                and .05<=rx<=.95 and .12<=ry<=.88):
            prices.append(price(compact.group(1),compact.group(2)))
        if NUMBER.fullmatch(txt):
            number=txt.strip()
            if rx<=.67:majors.append(number)
            if rx>=.56 and ry>=.4 and len(number)==2:cents.append(number)
    # Never mix an explicit decimal and incompatible geometric candidate.
    for major in majors:
        for minor in cents:
            if major!=minor:
                prices.append(price(major,minor))
    values=sorted(set(prices))
    if len(values)==1:
        return {'value':values[0],'status':'vision_price_candidate_review_only',
                'method':'explicit_or_spatial','ocr_lines':[x[0]['text'][:100] for x in selected]}
    return {'value':None,'status':'vision_ambiguous_review_only' if values else 'vision_unreadable_review_only',
            'candidates':values,'ocr_lines':[x[0]['text'][:100] for x in selected]}


def fuse(vision: str | None, local: str | None) -> dict:
    if vision and local:
        if vision == local:
            return {'value':vision,'status':'dual_engine_agreement_review_only'}
        return {'value':None,'status':'ocr_disagreement_review_only','alternatives':[vision,local]}
    if vision:return {'value':vision,'status':'vision_only_review_only'}
    if local:return {'value':local,'status':'tesseract_only_review_only'}
    return {'value':None,'status':'unresolved_no_ocr_evidence_review_only'}


def prior_signals(lines: list[dict]) -> list[dict]:
    output=[]
    for line in lines:
        value=line.get('text','')
        marker=PRIOR.search(value)
        if not marker:continue
        tail=value[marker.end():]
        matched=DECIMAL.findall(tail)
        vals=[price(a,b) for a,b in matched]
        kind=('range' if len(vals)>=2 and re.search(r'\d\s*[-–]\s*\d',tail)
              else 'single' if len(vals)==1 else 'ambiguous')
        output.append({'text':value[:150],'kind':kind,'amounts_nok_review_only':vals if kind!='ambiguous' else [],
                       'box':line.get('box'),'review_only':True})
    return output


def local_prices(doc: dict) -> dict[tuple[int,int],str | None]:
    if doc.get('week')!='2026-W41' or doc.get('pages_scanned')!=29:
        raise ValueError('Expected W41 local plaque OCR 29 pages')
    return {(int(page['page']),int(row['blue_plaque_index'])):row['current_price_nok_review_only']
            for page in doc['pages'] for row in page['observations']}


def checked_vision(doc: dict) -> dict[int,list[dict]]:
    if doc.get('format')!='nutrivalue_ocr_lines_v1' or doc.get('week')!='2026-W41' or doc.get('page_count')!=29:
        raise ValueError('Expected W41 Vision OCR 29 pages; W42 data forbidden')
    pages=doc.get('pages')
    if not isinstance(pages,list) or len(pages)!=29 or doc.get('provider')!='google_cloud_vision':
        raise ValueError('Missing 29 Google Vision pages')
    out={}
    for index,page in enumerate(pages,1):
        if page.get('page')!=index or page.get('status')!='processed' or not isinstance(page.get('lines'),list):
            raise ValueError(f'Incomplete/unsorted Vision OCR: page {index}')
        out[index]=page['lines']
    return out


def prior_amount_coverage(visual_reference: dict, vision_priors: dict, red: dict | None = None) -> dict:
    """Multiset page/amount coverage only, NOT position or product identity.

    Duplicate observations from Vision and Tesseract count at most once.
    """
    if visual_reference.get('week') != '2026-W41' or visual_reference.get('record_count') != 39:
        raise ValueError('Expected 39 manually reviewed W41 prior/price/caption references')
    expected=Counter()
    for r in visual_reference['records']:
        v=r['printed_prior_nok']
        if r.get('prior_kind')=='range':
            if isinstance(v,str) and '-' in v:
                amounts=tuple(v.split('-',1))
            elif isinstance(v,list) and len(v)==2:
                amounts=tuple(v)
            else:raise ValueError('Invalid prior range')
        else:amounts=(v,)
        expected[(r['page'],amounts)]+=1
    def counts(rows):
        c=Counter()
        for item in rows:
            if not item['amounts_nok_review_only']:continue
            c[(item['page'],tuple(item['amounts_nok_review_only']))]+=1
        return c
    vision_rows=[dict(row,page=int(p)) for p,rows in vision_priors.items() for row in rows]
    vision_counts=counts(vision_rows)
    red_rows=[]
    if red is not None:
        if red.get('week')!='2026-W41' or not isinstance(red.get('records'),list):
            raise ValueError('Invalid red OCR candidates')
        red_rows=red['records']
    red_counts=counts(red_rows)
    vis_matched=sum(min(v,vision_counts[k]) for k,v in expected.items())
    red_matched=sum(min(v,red_counts[k]) for k,v in expected.items())
    union_matched=sum(min(v,max(vision_counts[k],red_counts[k])) for k,v in expected.items())
    return {'visual_reference_ads':39,'vision_matched_by_page_amount_only':vis_matched,
            'red_ocr_matched_by_page_amount_only':red_matched,
            'union_matched_by_page_amount_only':union_matched,
            'missing_by_page_amount_only':39-union_matched,
            'important_limit':'Same-page price match is NOT spatial/proven product identity; duplicates can be indistinguishable.'}


def run(reference:dict,local:dict,vision:dict):
    if reference.get('catalog_week')!='2026-W41' or len(reference.get('rows',[]))!=61:
        raise ValueError('Expected W41 61-plaque visual reference')
    lprices=local_prices(local)
    vpages=checked_vision(vision)
    results=[]; count=Counter()
    for item in reference['rows']:
        page=int(item['page']);idx=int(item['blue_plaque_index']);truth=item['visual_label_nok']
        box=item['blue_plaque_box'];v=vision_plaque_price(vpages[page],box)
        local_amount=lprices.get((page,idx))
        fused=fuse(v['value'],local_amount)
        judged=('non_price' if truth is None else 'correct' if fused['value']==truth else
                'incorrect' if fused['value'] else 'abstained')
        count[judged]+=1
        results.append({'page':page,'blue_plaque_index':idx,'vision':v,'local_price_nok':local_amount,
                        'fused':fused,'visual_label_nok':truth,'evaluation_outcome':judged,
                        'product_identity_verified':False,'historical_price_verified':False,
                        'database_writes':False})
    priors={str(p):prior_signals(lines) for p,lines in vpages.items()}
    summary={**dict(count),'visual_price_plaques':sum(r['visual_label_nok'] is not None for r in results),
             'explicit_vision_prior_signals':sum(len(v) for v in priors.values())}
    return results,priors,summary


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vision',type=Path,required=True)
    ap.add_argument('--local',type=Path,required=True)
    ap.add_argument('--reference',type=Path,required=True)
    ap.add_argument('--prior-reference',type=Path)
    ap.add_argument('--red-ocr',type=Path)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args(argv)
    if not private_path(args.out_dir) or args.out_dir.exists() or args.out_dir.is_symlink():
        ap.error('Output must be a NEW folder under data/private/')
    input_paths=(args.vision,args.local,args.reference)+tuple(p for p in (args.prior_reference,args.red_ocr) if p)
    if any(not p.is_file() or p.is_symlink() for p in input_paths):
        ap.error('Vision, local OCR and reference must be existing, regular non-symlink files')
    try:
        vision=json.loads(args.vision.read_text(encoding='utf-8'))
        local=json.loads(args.local.read_text(encoding='utf-8'))
        reference=json.loads(args.reference.read_text(encoding='utf-8'))
        rows,priors,s=run(reference,local,vision)
        prior_metrics=prior_amount_coverage(
            json.loads(args.prior_reference.read_text(encoding='utf-8')),priors,
            json.loads(args.red_ocr.read_text(encoding='utf-8')) if args.red_ocr else None
        ) if args.prior_reference else None
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:
        ap.error(f'Invalid data: {str(e)[:180]}')
    args.out_dir.mkdir(parents=True)
    data={'schema':'nutrivalue_w41_ocr_evidence_comparison_v1','week':'2026-W41',
          'summary':s,'prior_reference_coverage':prior_metrics,'plaque_evidence':rows,'vision_prior_evidence':priors,
          'visual_labels_used_for_scoring_only':True,'provider_calls':0,'database_writes':False,
          'historical_prices_verified':0,'tjek_product_identities_verified':0,
          'source_sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in input_paths}}
    (args.out_dir/'comparison.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (args.out_dir/'review-queue.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['page','blue_plaque_index','status','vision','tesseract','printed_label_for_evaluation','evaluation_outcome'])
        for r in rows:
            if r['evaluation_outcome'] in ('incorrect','abstained'):
                w.writerow([r['page'],r['blue_plaque_index'],r['fused']['status'],
                            r['vision']['value'],r['local_price_nok'],r['visual_label_nok'],r['evaluation_outcome']])
    lines=['# W41 — offline Vision/Tesseract OCR evaluation','',
           f"59 actual price plaques; {s.get('correct',0)} correct, {s.get('incorrect',0)} incorrect, {s.get('abstained',0)} abstained; {s.get('non_price',0)} non-price detections.",
           f"Vision FØR evidence lines: {s['explicit_vision_prior_signals']} (not exhaustive; no product identity).",
           f'Prior reference amount-only coverage: {prior_metrics}' if prior_metrics else 'No prior-price reference supplied.', '',
           'This measures ONLY already-detected blue plaques. Separate evaluation is required for all 39 visually reviewed FØR→price→caption ads, including 20 outside the detector.',
           'No OCR API calls / no LLM / no DB writes in this evaluator. All OCR outputs remain review-only.',
           '', '## Wrong or unreadable automated prices']
    lines += [f"- p{r['page']:02d} badge {r['blue_plaque_index']}: label {r['visual_label_nok']}; Vision {r['vision']['value']}; Tesseract {r['local_price_nok']}; {r['fused']['status']}"
              for r in rows if r['evaluation_outcome'] in ('incorrect','abstained')]
    (args.out_dir/'audit.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('W41 offline Vision+Tesseract comparison:',s)
    print('No provider calls | no DB writes | no SKU verification')
    print('Report:',args.out_dir/'audit.md')
    return 0

if __name__=='__main__':raise SystemExit(main())
