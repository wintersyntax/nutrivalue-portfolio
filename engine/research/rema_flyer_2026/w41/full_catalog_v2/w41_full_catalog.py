#!/usr/bin/env python3
"""W41 offline candidate discovery and full-page human-review quality gate.

Inference: 29 cached Vision pages, V3 FØR ad evidence, original page pixels.
Evaluation: held out of inference; mandatory independent 29-page human review.
No Tjek, network, API, database writes or LLM calls. Never assigns a SKU.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

from w41_ab_review import checked_vision, image_plan, asbox, center, price_in_line, near_top, amount, EXCLUDE_TEXT, PRIOR_MARK

WEEK = '2026-W41'
V3_SCHEMA = 'nutrivalue_w41_ab_evidence_v3_paddle_review_only'
CANDIDATE_FIELDS = ['candidate_id','page','candidate_source','printed_current_nok','prior_nok','proposed_product_text','anchor_x','anchor_y','evidence_status','promotion_flags','is_ad','price_correct','product_correct','duplicate_of','review_notes']
PAGE_FIELDS = ['page','review_complete','notes']
MISS_FIELDS = ['page','product','printed_current_nok','prior_nok','notes']
HEAD_RE = re.compile(r'(?i)(?:\b(?:RABATT|PARTIVARE|L[ÅA]ST\s+PRIS|SLUTTSUMMEN|PLUKK\s+OG\s+MIKS|PRISKUTT|BLEIEKUTT|BONUS|TRUKKET\s+FRA|SPAR\s+\d|GODE\s+HAV)\b|(?<!\d)\d{1,2}\s*%)')
MULTIBUY_RE = re.compile(r'(?i)(?:\b[2-9]\s*FOR\b|\bPLUKK\s+OG\s+MIKS\b|\+\s*PANT)')
NOISE_RE = re.compile(r'(?i)\b(?:PR\.?\s*(?:KG|L|STK)|L[ÅA]ST\s+PRIS|REMA\s+1000|F[ØO]R|TIDLIGERE|KUNDEAVIS)\b')
QTY_RE = re.compile(r'(?i)\b\d+(?:[,.]\d+)?\s*(?:g|kg|ml|l|stk|pk)\b')


def assert_local_path(p:Path):
    if p.is_symlink(): raise ValueError('Symlinks are not supported for inputs/outputs')
    if not p.is_file(): raise ValueError(f'Missing file: {p}')


def sha(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_v3(doc, vision_fp:str):
    if (doc.get('schema') != V3_SCHEMA or doc.get('week') != WEEK or doc.get('pages')!=29
        or doc.get('tjek_catalog_linked') is not False or doc.get('visual_labels_used_in_inference') is not False
        or doc.get('tjek_identity_verified') is not False or doc.get('database_writes') is not False):
        raise ValueError('V3 must be W41 unlinked, review-only evidence')
    if doc.get('v3_source_sha256',{}).get('vision') != vision_fp:
        raise ValueError('Vision file SHA-256 differs from the V3 source')
    rows=doc.get('candidates')
    if not isinstance(rows,list) or len(rows)!=39:raise ValueError('V3 must contain exactly 39 W41 FØR candidates')
    for r in rows:
        if type(r.get('page')) is not int or not 1<=r['page']<=29:
            raise ValueError('Out-of-week page')
        if not asbox(r.get('prior',{}).get('box')):
            raise ValueError('Missing V3 FØR box')
        if r.get('tjek_identity_verified') is not False or r.get('database_writes') is not False:
            raise ValueError('V3 contains disallowed verified/product values')
        current=r.get('printed_current_ocr') or {}
        if current.get('value') and amount(current['value']) is None:
            raise ValueError('Invalid current price in V3')
    return rows


def rect_to_box(r):
    return {'x':r['x0'],'y':r['y0'],'width':r['x1']-r['x0'],'height':r['y1']-r['y0']}


def near_price(a,b, dx=.075,dy=.08):
    ax,ay=center(a);bx,by=center(b)
    return abs(ax-bx)<dx and abs(ay-by)<dy


def intersect_area(a,b):
    return max(0,min(a['x']+a['width'],b['x']+b['width'])-max(a['x'],b['x']))*max(0,min(a['y']+a['height'],b['y']+b['height'])-max(a['y'],b['y']))


def text_price_rows(lines):
    """Only prices printed as large standalone glyphs, never unit-price prose."""
    hits=[]
    for i,l in enumerate(lines):
        b=asbox(l.get('box'))
        if not b or b['height']<.029 or b['width']<.048:continue
        value=price_in_line(l)
        if value is None:continue
        hits.append({'index':i,'value':value,'box':b,'text':l.get('text',''),'confidence':l.get('confidence')})
    return hits


def candidate_box(record):
    return asbox(record.get('price_box') or record.get('prior_box'))


def classify_plaque_price(rect, prices):
    """Read OCR on the CV region; any two distinct amounts are a conflict."""
    box=rect_to_box(rect)
    hits=[]
    for line in prices:
        b=line['box']
        overlap=intersect_area(b,box)/max(b['width']*b['height'],1e-9)
        if overlap>=.29 or (near_price(b,box, .065,.055) and overlap>=.15):
            hits.append((overlap,line))
    values={h['value'] for _,h in hits}
    if len(values)>1:
        return None,'multiple_ocr_prices_review_only',hits
    if len(values)==1:
        return next(iter(values)),'plaque_vision_price_review_only',hits
    return None,'plaque_price_unreadable_review_only',hits


def link_captions(lines, price_box, other_anchor_boxes):
    """Conservative geometry-only candidates: never a verified product identity."""
    if not price_box:return []
    px,py=center(price_box)
    peers=[center(b) for b in other_anchor_boxes if not near_price(b,price_box,.045,.05)]
    same_row=[(x,y) for x,y in peers if abs(y-py)<.095 and abs(x-px)>.065]
    left=max([max(0,(px+x)/2) for x,y in same_row if x<px]+[max(0,px-.265)])
    right=min([min(1,(px+x)/2) for x,y in same_row if x>px]+[min(1,px+.265)])
    # lower next ad limits a product caption's bottom bound
    lower=[y for x,y in peers if abs(x-px)<.17 and py-.35<y<py-.09]
    min_y=max(.044,max(lower)+.013 if lower else .044)
    possibilities=[]
    for i,raw in enumerate(lines):
        b=asbox(raw.get('box'));s=str(raw.get('text','')).strip()
        if not b or not s or len(s)>180 or not re.search('[A-Za-zÆØÅæøå]{3}',s):continue
        if NOISE_RE.search(s) or HEAD_RE.fullmatch(s) or price_in_line(raw) is not None:continue
        x,y=center(b)
        if not (min_y<=y<py-.009 and y>py-.35):continue
        overlap=max(0,min(b['x']+b['width'],right)-max(b['x'],left)) / b['width']
        if overlap<.78:continue
        # OCR line can contain product and unit price, but pure pricing prose is not product.
        upper=s.upper()
        strong=int(bool(QTY_RE.search(s)))+int(bool(re.search(r'[A-ZÆØÅ]{4}',upper)))
        if len(s)<5:continue
        possibilities.append(((-strong,abs(py-y),abs(x-px)),{'ocr_line_index':i,'text':s,'box':b,'source':'vision_caption_geometry_review_only'}))
    possibilities.sort(key=lambda x:x[0])
    return [v for _,v in possibilities[:4]]


def assign_v3_to_plaque(v3s,plaques):
    """One-to-one; two distinct priors must not swallow the same blue region."""
    claims=defaultdict(list)
    for idx,row in enumerate(v3s):
        prior_box=row['prior']['box']
        cur=asbox((row.get('printed_current_ocr') or {}).get('box'))
        matches=[]
        for pidx,rect in enumerate(plaques):
            pb=rect_to_box(rect)
            if row.get('blue_plaque_match') and row.get('blue_plaque') and near_price(pb,rect_to_box(row['blue_plaque']),.020,.020):
                matches.append(pidx)
            elif near_top({'box':prior_box},rect):
                matches.append(pidx)
            elif cur and near_price(cur,pb,.025,.025):
                matches.append(pidx)
        if len(set(matches))==1:claims[matches[0]].append(idx)
    return {idx:users[0] for idx,users in claims.items() if len(users)==1}


def promotion_flags(lines, price_box):
    if not price_box:return []
    px,py=center(price_box);flags=[]
    for l in lines:
        b=asbox(l.get('box')); t=str(l.get('text',''))
        if not b:continue
        x,y=center(b)
        if abs(x-px)>.25 or not (-.11<y-py<.13):continue
        if MULTIBUY_RE.search(t):flags.append('MULTIBUY_OR_PANT_CONDITION_REVIEW_ONLY')
        if re.search(r'(?i)\bSLUTTSUMMEN\b',t):flags.append('COMPOSITE_MEAL_TOTAL_NOT_ITEM_PRICE')
        if re.fullmatch(r'(?i)\s*PR\s*\.?\s*(?:KG|KILO)\s*',t) and abs(x-px)<.14:
            flags.append('PRICE_PER_KG_BADGE_REVIEW_ONLY')
    return sorted(set(flags))


def build(vision, v3, images, src):
    pages=checked_vision(vision)
    image_plan(pages, images)  # must verify all 29 image SHA256, no OCR re-call
    for pg in pages:
        for ln in pg['lines']:
            if not asbox(ln.get('box')):raise ValueError('Invalid Vision OCR line geometry')
    v3_by_page=defaultdict(list)
    for r in v3:v3_by_page[r['page']].append(r)
    sys.path.insert(0,str(src))
    try:
        from ad_layout_regions import _blue_price_badge_boxes
    finally: sys.path.pop(0)
    all_candidates=[]; campaigns=[]; counts=Counter()
    for page in pages:
        n=page['page'];lines=page['lines'];prices=text_price_rows(lines)
        plaques=list(_blue_price_badge_boxes(images/f'page-{n:02d}.jpg'))
        priors=v3_by_page[n]
        covered_plaque=assign_v3_to_plaque(priors,plaques)
        records=[]
        for v in priors:
            p=v.get('printed_current_ocr') or {}
            q=asbox(p.get('box')); pb=asbox(v['prior']['box'])
            val=amount(p['value']) if p.get('value') else None
            status='v3_prior_price_caption_review_only' if (val and v.get('nearby_ocr_caption_evidence')) else 'v3_partial_evidence_review_only'
            records.append({'candidate_source':'v3_for_anchor','page':n,'printed_current_nok':val,'prior_nok':list(v['prior'].get('amounts',[])),
              'prior_text':v['prior'].get('text',''),'price_box':q,'prior_box':pb,'caption_candidates':copy_captions(v.get('nearby_ocr_caption_evidence',[])),
              'evidence_status':status,'source_evidence':v.get('targeted_paddle_evidence',{}).get('status') or p.get('status'),
              'needs_review':True,'tjek_identity_verified':False})
        for pidx,rect in enumerate(plaques):
            if pidx in covered_plaque:continue
            box=rect_to_box(rect)
            val,status,hits=classify_plaque_price(rect,prices)
            records.append({'candidate_source':'cv_large_blue','page':n,'printed_current_nok':val,'prior_nok':[],
              'prior_text':'','price_box':box,'prior_box':None,'caption_candidates':[],
              'evidence_status':status,'source_evidence':'opencv_blue_plaque_and_vision_ocr','needs_review':True,
              'vision_price_line_indices':[h['index'] for _,h in hits],'tjek_identity_verified':False})
        for hit in prices:
            b=hit['box'];cx,cy=center(b)
            matching=[]
            for record in records:
                rb=candidate_box(record)
                if rb is None:continue
                # Same physical current-price box, not same price value.
                if record['price_box'] and (near_price(b,rb,.043,.045) or intersect_area(b,rb)/ (b['width']*b['height']) >= .35):
                    matching.append(record)
                elif record['prior_box'] and 0< center(record['prior_box'])[1]-cy <.13 and abs(center(record['prior_box'])[0]-cx)<.12:
                    matching.append(record)
            if matching:continue
            # Missing small/nonstandard blue region; cannot confirm offer or print layout without page review.
            records.append({'candidate_source':'vision_large_price','page':n,'printed_current_nok':hit['value'],'prior_nok':[],
              'prior_text':'','price_box':b,'prior_box':None,'caption_candidates':[],
              'evidence_status':'vision_unmatched_large_price_review_only',
              'source_evidence':f"vision_line_{hit['index']}",'needs_review':True,'tjek_identity_verified':False})
        anchors=[candidate_box(r) for r in records if candidate_box(r)]
        for r in records:
            if not r['caption_candidates']:
                r['caption_candidates']=link_captions(lines,r['price_box'],[b for b in anchors if b])
            r['proposed_product_text']=r['caption_candidates'][0]['text'] if r['caption_candidates'] else ''
            r['promotion_flags']=promotion_flags(lines,r['price_box'])
            r['review_only']=True
        # Campaign hints are *not* counted as price-bearing ads: may cover multiple SKUs.
        for idx,ln in enumerate(lines):
            if HEAD_RE.search(str(ln.get('text',''))) and not PRIOR_MARK.search(str(ln.get('text',''))):
                campaigns.append({'page':n,'ocr_line_index':idx,'text':ln['text'],'box':ln['box'],
                    'status':'campaign_text_signal_review_only_not_unique_ad'})
        records.sort(key=lambda r:(-(center(candidate_box(r))[1] if candidate_box(r) else 0),center(candidate_box(r))[0] if candidate_box(r) else 0,r['candidate_source']))
        for idx,r in enumerate(records,1):
            r['candidate_id']=f'W41-P{n:02d}-C{idx:03d}'
            counts[r['candidate_source']]+=1
            all_candidates.append(r)
    return {'schema':'nutrivalue_w41_full_catalog_candidates_v1','week':WEEK,'pages':29,
        'counts':dict(counts),'candidates':all_candidates,'campaign_text_signals':campaigns,
        'candidate_count':len(all_candidates),'manual_full_catalog_reference_complete':False,
        'full_catalog_recall':None,'correct_product_price_recall':None,
        'tjek_identity_verified':False,'data_writes':False,'review_only':True}


def copy_captions(caps):
    return [{'text':str(x.get('text',''))[:180],'ocr_line_index':x.get('line_index'),'box':asbox(x.get('box')),
              'source':x.get('caption_provenance','v3_vision_caption_review_only')} for x in caps[:6]]


def make_review_files(doc,out):
    with (out/'review-candidates.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=CANDIDATE_FIELDS);w.writeheader()
        for item in doc['candidates']:
            bx=candidate_box(item) or {'x':0,'y':0,'width':0,'height':0}
            x,y=center(bx)
            w.writerow({'candidate_id':item['candidate_id'],'page':item['page'],'candidate_source':item['candidate_source'],
                'printed_current_nok':item['printed_current_nok'] or '',
                'prior_nok':';'.join(item['prior_nok']), 'proposed_product_text':item['proposed_product_text'],
                'anchor_x':f'{x:.5f}','anchor_y':f'{y:.5f}', 'evidence_status':item['evidence_status'],'promotion_flags':';'.join(item.get('promotion_flags',[])),
                'is_ad':'','price_correct':'','product_correct':'','duplicate_of':'','review_notes':''})
    with (out/'page-completion.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=PAGE_FIELDS);w.writeheader();w.writerows({'page':p,'review_complete':'','notes':''} for p in range(1,30))
    with (out/'missed-ads.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=MISS_FIELDS);w.writeheader()


def draw_overlays(doc,images,out):
    import cv2
    folder=out/'overlays';folder.mkdir()
    by_page=defaultdict(list)
    for r in doc['candidates']:by_page[r['page']].append(r)
    for page in range(1,30):
        im=cv2.imread(str(images/f'page-{page:02d}.jpg'))
        if im is None:raise ValueError(f'Image missing/corrupt page {page}')
        h,w=im.shape[:2]
        for r in by_page[page]:
            box=candidate_box(r)
            if not box:continue
            # Small, neutral monochrome label to avoid obscuring flyers.
            x=int(box['x']*w); y=int((1-(box['y']+box['height']))*h)
            x2=int((box['x']+box['width'])*w);y2=int((1-box['y'])*h)
            x,y,x2,y2=max(0,x),max(0,y),min(w-1,x2),min(h-1,y2)
            cv2.rectangle(im,(x,y),(x2,y2),(10,10,10),1)
            lbl=r['candidate_id'].split('-')[-1] + ':' + (r['printed_current_nok'] or '?')
            cv2.putText(im,lbl,(x,max(14,y-3)),cv2.FONT_HERSHEY_SIMPLEX,.29,(0,0,0),2,cv2.LINE_AA)
            cv2.putText(im,lbl,(x,max(14,y-3)),cv2.FONT_HERSHEY_SIMPLEX,.29,(255,255,255),1,cv2.LINE_AA)
        cv2.imwrite(str(folder/f'page-{page:02d}.jpg'),im,[cv2.IMWRITE_JPEG_QUALITY,85])


def write_html(doc,out):
    import html
    bypage=defaultdict(list)
    for r in doc['candidates']:bypage[r['page']].append(r)
    parts=['<!doctype html><html lang="hr"><meta charset="utf-8"><title>W41 offline full-catalog review</title>',
      '<style>body{font:14px system-ui;margin:1.5rem;color:#222}section{border-top:1px solid #bbb;padding:1rem 0}img{max-height:900px;max-width:100%}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:4px;vertical-align:top}summary{cursor:pointer}small{color:#555}</style>',
      '<h1>W41 — pregled 29 stranica / SVE JE REVIEW ONLY</h1>',
      '<p>Točkice i kvadrati označavaju OCR kandidate, ne potvrđene oglase. Provjeri i oglase BEZ oznake; upiši ih u missed-ads.csv. '
      'Odgovori na sve redove u review-candidates.csv i pregledaj cijelu stranicu prije nego što page-completion.csv označiš YES.</p>',
      f'<p><b>{doc["candidate_count"]}</b> automatskih kandidata; naziv proizvoda i cijena nisu potvrđeni. '
      'Bez nezavisne provjere svih stranica ne smije se računati potpuna pokrivenost.</p>']
    for p in range(1,30):
        parts.append(f'<section><details><summary><b>Stranica {p:02d}</b> — {len(bypage[p])} kandidata</summary>')
        parts.append(f'<img loading="lazy" src="overlays/page-{p:02d}.jpg" alt="Page {p}">')
        parts.append('<table><tr><th>ID</th><th>Izvor</th><th>Cijena</th><th>Predloženi naziv</th><th>Status</th></tr>')
        for r in bypage[p]:
            esc=lambda x:html.escape(str(x or ''))
            parts.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (r['candidate_id'],r['candidate_source'],r['printed_current_nok'],r['proposed_product_text'][:120],r['evidence_status']+' '+','.join(r.get('promotion_flags',[]))))+'</tr>')
        parts.append('</table></details></section>')
    parts.append('</html>')
    (out/'review.html').write_text('\n'.join(parts),encoding='utf-8')


def read_csv(path,required):
    with path.open(newline='',encoding='utf-8-sig') as f:
        r=csv.DictReader(f)
        if not set(required).issubset(set(r.fieldnames or [])):raise ValueError(f'Wrong columns: {path}')
        return list(r)


def score_review(doc, review_rows,page_rows,missed_rows):
    """Ground-truth review must inspect ALL pages and account for all candidates/misses."""
    ids={r['candidate_id'] for r in doc['candidates']}
    if len(review_rows)!=len(ids) or {r['candidate_id'] for r in review_rows}!=ids:
        raise ValueError('review-candidates.csv must include each candidate ID exactly once')
    if len(page_rows)!=29 or {r['page'] for r in page_rows}!={str(i) for i in range(1,30)}:
        raise ValueError('page-completion.csv requires pages 1–29 exactly once')
    all_complete=all(x['review_complete'].strip().upper()=='YES' for x in page_rows)
    validated=0;correct_combo=0;unreviewed=0;nonads=0;dups=0
    for r in review_rows:
        state=r['is_ad'].strip().upper()
        if state not in ('YES','NO','DUPLICATE','UNCERTAIN'):
            unreviewed+=1;continue
        if state=='YES':
            pc=r['price_correct'].strip().upper();tc=r['product_correct'].strip().upper()
            if pc not in ('YES','NO') or tc not in ('YES','NO'):
                unreviewed+=1;continue
            if r['duplicate_of'].strip():raise ValueError('YES candidate cannot also be duplicate')
            validated+=1;correct_combo+=int(pc=='YES' and tc=='YES')
        elif state=='DUPLICATE':
            d=r['duplicate_of'].strip()
            if not d or d==r['candidate_id'] or d not in ids:raise ValueError('Duplicate must point to another candidate ID')
            dups+=1
        elif state=='NO': nonads+=1
        else: unreviewed+=1
    missed=[]
    for r in missed_rows:
        if not r['page'].strip() and not r['product'].strip():continue
        pg=r['page'].strip();name=r['product'].strip()
        if pg not in {str(i) for i in range(1,30)} or not name:
            raise ValueError('Missed row must have valid page and product')
        missed.append(r)
    denominator=validated+len(missed)
    complete=all_complete and unreviewed==0 and denominator>0
    return {'status':'FULL_CATALOG_RECALL_MEASURED' if complete else 'NOT_MEASURED_REVIEW_INCOMPLETE',
       'pages_reviewed':sum(x['review_complete'].strip().upper()=='YES' for x in page_rows),
       'candidate_rows_total':len(ids),'candidate_rows_unreviewed_or_ambiguous':unreviewed,
       'valid_unique_ads_recovered':validated,'candidate_non_ads':nonads,'duplicate_candidates':dups,
       'missed_valid_ads_added_by_review':len(missed), 'reference_ads_denominator':denominator if complete else None,
       'advert_discovery_recall':round(validated/denominator,4) if complete else None,
       'correct_product_and_price_recall':round(correct_combo/denominator,4) if complete else None,
       'correct_product_and_price_count':correct_combo, 'automatically_verified_tjek_identities':0,
       'note':'A reviewed candidate with uncertain price/name must be marked NO in the corresponding correctness column. '
              'This metric is a human-reviewed local score, NOT automatic confirmation.'}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vision',type=Path,required=True)
    ap.add_argument('--ab-v3',type=Path,required=True)
    ap.add_argument('--images',type=Path,required=True)
    ap.add_argument('--src',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args(argv)
    for path in [a.vision,a.ab_v3]:assert_local_path(path)
    if not a.images.is_dir() or not a.src.is_dir() or a.out_dir.exists():ap.error('Missing images/source or output dir already exists')
    if not str(a.out_dir).startswith('data/private/'):
        ap.error('For privacy, --out-dir must be relative under data/private/')
    try:
        vision=json.loads(a.vision.read_text(encoding='utf-8'));v3=json.loads(a.ab_v3.read_text(encoding='utf-8'))
        candidates=validate_v3(v3,sha(a.vision))
        doc=build(vision,candidates,a.images,a.src)
    except (ValueError,TypeError,KeyError,IndexError,FileNotFoundError) as e:
        ap.error('STOP: '+str(e)[:300])
    doc['evidence_sha256']={'vision':sha(a.vision),'ab_v3':sha(a.ab_v3)}
    a.out_dir.mkdir(parents=True)
    (a.out_dir/'candidates.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    make_review_files(doc,a.out_dir)
    draw_overlays(doc,a.images,a.out_dir)
    write_html(doc,a.out_dir)
    kinds=Counter(x['candidate_source'] for x in doc['candidates'])
    counts_by_page=Counter(x['page'] for x in doc['candidates'])
    report=['# W41 — All-pages advert *candidate* discovery (read-only)','',
      'Pages: 29/29 image hashes checked against cached Vision OCR.',
      f'Auto-discovered **candidate regions**: {len(doc["candidates"])}. Not a verified number of true ads.',
      '', '## Source breakdown',
      *[f'- {k}: {v}' for k,v in sorted(kinds.items())],
      '', '## Candidate regions per page',
      ', '.join(f'p{n:02d}={counts_by_page[n]}' for n in range(1,30)),
      '', f'OCR campaign signals (NOT unique ads): {len(doc["campaign_text_signals"])}',
      '', '## Quality gate',
      '**Full-catalog detection recall: NOT MEASURED.** All 29 original pages require human review.',
      '**Correct product+price recall: NOT MEASURED.** No genuine W41 Tjek snapshot.',
      '39 FØR-anchored proposals are included as OCR evidence; not a complete catalogue reference.',
      'Open review.html, inspect all 29 overlays, complete review-candidates.csv, page-completion.csv and missed-ads.csv.',
      'Then run score_w41_full_catalog.py. No score is produced from unreviewed data.',
      '', 'No API/LLM/Google calls. No production DB writes. No historic price verification.']
    (a.out_dir/'audit.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print('\n'.join(report))
if __name__=='__main__':main()
