#!/usr/bin/env python3
"""Read-only geometry-first refinement of saved W41 A/B proposals.

Input: V1 OCR candidate evidence plus COMPLETE cached W41 Vision geometry.
Inference NEVER reads visual labels or another week's Tjek offers. Distinct
printed ads remain separate. Output review-only, zero provider/DB calls.
"""
from __future__ import annotations
import argparse
from collections import Counter
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from w41_ab_review import asbox, center, checked_vision, price_in_line, score, private_path

WEEK='2026-W41'


def match_source(evidence:dict,vision_path:Path)->list[dict]:
    if (evidence.get('schema')!='nutrivalue_w41_ab_evidence_v1' or evidence.get('week')!=WEEK
            or evidence.get('pages')!=29 or evidence.get('visual_labels_used_in_inference') is not False
            or evidence.get('tjek_catalog_linked') is not False):
        raise ValueError('Expected unlinked 29-page W41 evidence v1; cross-catalog input refused')
    vision=json.loads(vision_path.read_text(encoding='utf-8'))
    pages=checked_vision(vision)
    fingerprint=hashlib.sha256(vision_path.read_bytes()).hexdigest()
    original=evidence.get('input_sha256',{})
    vision_hashes=[v for k,v in original.items() if k.endswith('ocr-lines-w41.json')]
    if len(vision_hashes)!=1 or vision_hashes[0]!=fingerprint:
        raise ValueError('Saved A/B evidence and cached Vision OCR hash differ')
    if len(evidence.get('candidates',[]))!=39 or sum(1 for c in evidence['candidates'] if c.get('page') not in range(1,30)):
        raise ValueError('Unexpected W41 baseline candidates')
    return pages


def price_evidence(lines:list[dict],prior:dict)->list[dict]:
    """Tight printed-price ROI; standalone 27200 is admissible if glyph-size fits.

    Never coerce brand+digits into a price, never fill missing cents.
    """
    pb=asbox(prior.get('box'))
    if not pb:return []
    cx,cy=center(pb)
    found=[]
    for idx,ln in enumerate(lines):
        b=asbox(ln.get('box'))
        if not b:continue
        x,y=center(b)
        if not (.025<cy-y<.165 and abs(cx-x)<.18):continue
        text=unicodedata.normalize('NFKC',str(ln.get('text',''))).strip()
        value=price_in_line(ln)
        if not value and re.fullmatch(r'[1-9]\d{3,4}',text) and b['height']>=.024 and b['width']>=.075:
            value=f'{int(text[:-2])}.{text[-2:]}'
        if value:
            found.append({'line_index':idx,'value':value,'box':b,
                 'source':'full_page_vision_price_geometry_review_only'})
    return found


def row_column_boundaries(current:dict,priors:list[dict],lines:list[dict]):
    """Build a local ad-cell from price and neighboring SAME-ROW prior/price boxes.

    A boundary is the midpoint between independent x anchors; candidates must
    fit fully in their column. Empty neighboring columns widen conservatively.
    """
    pb=current['prior']['box']; a_x,a_y=center(pb)
    curprice=current.get('printed_current_ocr') or {}
    prbox=asbox(curprice.get('box'))
    c_x,c_y=center(prbox) if prbox else (a_x,a_y-.06)
    # Prior markers are the strongest ad-cell separators in dense catalog grids.
    peers=[]
    for r in priors:
        if r is current or abs(center(r['prior']['box'])[1]-a_y)>.065:continue
        px,_=center(r['prior']['box'])
        if abs(px-a_x)>.085:peers.append(px)
    # Also use distinct PRICE markers near same printed-current horizontal band.
    for ln in lines:
        b=asbox(ln.get('box'))
        if not b or abs(center(b)[1]-c_y)>.09:continue
        if price_in_line(ln) is None:continue
        x=center(b)[0]
        if abs(x-c_x)>.17:peers.append(x)
    # Foreground price glyphs often sit toward RIGHT side of its product.
    base=c_x if prbox else a_x
    left_neighbors=[x for x in peers if x<min(a_x,base)-.09]
    right_neighbors=[x for x in peers if x>max(a_x,base)+.09]
    left=max((base+x)/2 for x in left_neighbors) if left_neighbors else max(0,min(a_x,base)-.33)
    right=min((base+x)/2 for x in right_neighbors) if right_neighbors else min(1,max(a_x,base)+.20)
    # Neighboring prior markers in same row can define boundaries even when
    # their OCR price is missing/merged with branded text.
    for x in peers:
        mid=(a_x+x)/2
        if x<a_x-.09:left=max(left,mid)
        elif x>a_x+.09:right=min(right,mid)
    if right-left<.13:
        left=max(0,min(a_x,base)-.19);right=min(1,max(a_x,base)+.10)
    # A caption of this row cannot be below a distinct lower-row prior marker.
    lower=[r['prior']['box']['y']+r['prior']['box']['height'] for r in priors
           if r is not current and center(r['prior']['box'])[1]<a_y-.095]
    cutoff=max(lower)+.013 if lower else .064
    # Strong vertical delimiter: next lower PRINTED price row.  This avoids
    # attaching KYLLING LÅRFILET from the next offer to the giant KYLLINGFILET
    # badge and stops mixing FRYSTE BURGERE with TERNET BACON below.
    lower_price_tops=[]
    for ln in lines:
        b=asbox(ln.get('box'))
        if b is None or price_in_line(ln) is None:continue
        line_x,line_y=center(b)
        if not left-.015 <= line_x <= right+.015:continue
        if c_y-.45 < line_y < c_y-.105:
            lower_price_tops.append(b['y']+b['height'])
    if lower_price_tops:
        cutoff=max(cutoff,max(lower_price_tops)+.018)
    return left,right,cutoff,c_y


EXCLUDED=re.compile(r'^(?:PR\s*\.?\s*(?:KG|STK|L)|ALLTID LAVE PRISER|REMA 1000|\d+%|GODE HAV)$',re.I)
NOISE=re.compile(r'(?i)\b(?:pr\s*\.\s*(?:kg|l|stk)|kundeavis)\b')


def product_caption_evidence(lines:list[dict],item:dict,priors:list[dict])->list[dict]:
    cur=item.get('printed_current_ocr') or {}
    if not cur.get('value'):return []
    left,right,lower,price_y=row_column_boundaries(item,priors,lines)
    results=[]
    for idx,ln in enumerate(lines):
        b=asbox(ln.get('box'))
        if not b:continue
        txt=str(ln.get('text','')).strip()
        if not txt or len(txt)>140 or EXCLUDED.fullmatch(txt) or not re.search(r'[A-Za-zÆØÅæøå]{3}',txt):continue
        if re.search(r'\b(?:F[ØO0]R|L[ÅA]ST PRIS)\b',txt,re.I) or price_in_line(ln) is not None:continue
        x,y=center(b)
        # Caption below price (or, for wide artwork, within large price badge's
        # vertical range), and ABOVE the next row's price/prior anchor.
        if not (lower < y < price_y-.016 and y>=.062):continue
        # Require most of OCR text to sit within a single proposed ad column.
        bx0,bx1=b['x'],b['x']+b['width']
        intersection=max(0,min(bx1,right)-max(bx0,left))
        if intersection/max(.001,b['width'])<.76:continue
        # Brand/marketing fragments are retained, but SKU-like lines rank first.
        upper=txt.upper()
        informative=bool(re.search(r'\b(?:\d{1,4}\s*(?:G|KG|ML|L|PK)|FROST|FRYST|FILET|SALAMI|KAFFE|PIZZA|HVETEMEL)\b',upper))
        nameish=(bool(re.search(r'[A-ZÆØÅ]{4,}',txt)) and len(txt)>=8)
        prefer=(2 if informative else 0)+(1 if nameish else 0)+(1 if len(txt)>=15 else 0)
        noise=1 if NOISE.search(txt) and not informative else 0
        rank=(-prefer+noise,abs(price_y-y),abs(x-center(item['prior']['box'])[0]))
        results.append((rank,{'line_index':idx,'text':txt,'box':b,'distance':round(price_y-y,5),
                         'inferred_ad_column':[round(left,5),round(right,5)],
                         'caption_provenance':'vision_within_same_ocr_ad_cell_review_only'}))
    results.sort(key=lambda a:a[0])
    return [r for _,r in results[:6]]


def refine(evidence:dict,pages:list[dict])->dict:
    out=copy.deepcopy(evidence)
    out['schema']='nutrivalue_w41_ab_evidence_v2'
    out['v1_counts_unchanged']=out.pop('counts',None)
    out.pop('reference_score_held_aside',None)
    out['v2_evaluator_has_no_visual_labels_in_inference']=True
    changes=[];new=Counter()
    for page in pages:
        rows=[r for r in out['candidates'] if r['page']==page['page']]
        if not rows:continue
        for it in rows:
            prev=it.get('printed_current_ocr') or {}
            all_price=price_evidence(page['lines'],it['prior'])
            # Rescue only if there is exactly one DISTINCT visible price in ROI.
            # Any V1 disagreement remains unresolved even if a model finds a value.
            if not prev.get('value') and prev.get('status')!='ocr_disagreement_review_only':
                distinct={r['value'] for r in all_price}
                if len(distinct)==1:
                    e=min(all_price,key=lambda r:abs(center(r['box'])[0]-center(it['prior']['box'])[0]))
                    it['printed_current_ocr']={'status':'v2_vision_compact_price_candidate_review_only',
                     'value':e['value'],'box':e['box'],'ocr_line':e['line_index'],
                     'source':e['source']}
                    changes.append((page['page'],it['prior']['text'],'price_rescued',e['value']))
            old_caps=it['nearby_ocr_caption_evidence']
            it['v1_caption_count']=len(old_caps)
            it['nearby_ocr_caption_evidence']=product_caption_evidence(page['lines'],it,rows)
            if it['nearby_ocr_caption_evidence']!=old_caps:changes.append((page['page'],it['prior']['text'],'caption_region_refined',len(it['nearby_ocr_caption_evidence'])))
            val=(it.get('printed_current_ocr') or {}).get('value')
            it['status']=('ambiguous_prior_review_only' if it['prior']['kind'] in ('ambiguous','conflict') else
              'ocr_price_caption_candidate_review_only' if val and it['nearby_ocr_caption_evidence'] else
              'ocr_price_no_caption_review_only' if val else
              'ambiguous_price_review_only' if it.get('printed_current_ocr') else 'prior_only_review_only')
            new[it['status']]+=1
    out['counts']={'prior_anchors':len(out['candidates']),'not_matched_to_large_blue_plaque':sum(not r['blue_plaque_match'] for r in out['candidates']),**new}
    out['v2_changes']=[{'page':page,'prior_text':prior,'action':action,'detail':value} for page,prior,action,value in changes]
    out['tjek_identity_verified']=False
    out['database_writes']=False
    return out



def reference_only_review_rows(predicted:dict,ref:dict)->list[dict]:
    """DIAGNOSTICS AFTER inference only. Reference values never used to choose a price/caption."""
    from w41_ab_review import _tokens
    consumed=set();results=[]
    for expected in ref['records']:
        prior=expected['printed_prior_nok']
        old=tuple(prior.split('-')) if isinstance(prior,str) and '-' in prior else tuple(prior) if isinstance(prior,list) else (prior,)
        wanted=_tokens(expected['visual_product_caption'])
        candidates=[(j,r) for j,r in enumerate(predicted['candidates']) if j not in consumed
                    and r['page']==expected['page'] and tuple(r['prior']['amounts'])==old]
        current=[(j,r) for j,r in candidates if (r.get('printed_current_ocr') or {}).get('value')==expected['printed_current_nok']]
        matched=[]
        for j,r in current:
            observed=set().union(*(_tokens(hit['text']) for hit in r['nearby_ocr_caption_evidence'])) if r['nearby_ocr_caption_evidence'] else set()
            if len(wanted & observed)>=max(1,min(2,len(wanted))) and len(wanted & observed)/max(1,len(wanted))>=.50:
                matched.append((j,r))
        if matched:
            j,r=matched[0];consumed.add(j);state='strict_reference_overlap_review_only'
        elif not candidates:state='prior_amount_missing_or_conflicting_review_only'
        elif not current:state='current_price_missing_or_conflicting_review_only'
        else:state='caption_lexical_mismatch_review_only'
        source=(matched or current or candidates)
        possible=source[0][1] if source else None
        vals=(possible.get('printed_current_ocr') or {}) if possible else {}
        results.append({'page':expected['page'],'reference_prior':prior,
            'reference_printed_price':expected['printed_current_nok'],
            'reference_product_caption':expected['visual_product_caption'],
            'observed_printed_price':vals.get('value',''),
            'observed_candidate_captions':' | '.join(x['text'][:100] for x in possible['nearby_ocr_caption_evidence'][:4]) if possible else '',
            'reference_only_issue':state})
    return results


def validate_ref(ref):
    if ref.get('week')!='2026-W41' or ref.get('record_count')!=39 or len(ref.get('records',[]))!=39:
        raise ValueError('Held-out reference must be W41 39-ad reference')


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vision',type=Path,required=True)
    ap.add_argument('--v1-evidence',type=Path,required=True)
    ap.add_argument('--reference',type=Path)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args(argv)
    if not private_path(args.out_dir) or args.out_dir.exists():ap.error('Output must be NEW under data/private/')
    for path in (args.vision,args.v1_evidence,args.reference):
        if path and (not path.is_file() or path.is_symlink()):ap.error('Missing/symlink input')
    try:
        old=json.loads(args.v1_evidence.read_text(encoding='utf-8'))
        pages=match_source(old,args.vision)
        new=refine(old,pages)
        if args.reference:
            ref=json.loads(args.reference.read_text(encoding='utf-8'))
            validate_ref(ref)
            new['v2_reference_scoring_only']=score(new,ref)
            reference_rows=reference_only_review_rows(new,ref)
        new['v2_vision_sha256']=hashlib.sha256(args.vision.read_bytes()).hexdigest()
        new['v2_evidence_sha256']=hashlib.sha256(args.v1_evidence.read_bytes()).hexdigest()
    except (ValueError,TypeError,KeyError,IndexError,json.JSONDecodeError) as e:
        ap.error(f'STOP: {str(e)[:220]}')
    args.out_dir.mkdir(parents=True)
    (args.out_dir/'w41-ab-evidence-v2.json').write_text(json.dumps(new,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report=['# W41 A/B V2 — geometry-refined OCR candidate evidence','',f"Prior anchors: {new['counts']['prior_anchors']}",
       f"Beyond large blue: {new['counts']['not_matched_to_large_blue_plaque']}",
       f"Candidate counts: {new['counts']}",f"Reference scoring: {new.get('v2_reference_scoring_only','no reference provided')}",
       '', '## Not accepted for automatic publication',
       'All W41 OCR pairings remain REVIEW ONLY; page/amount/caption overlap is not verified Tjek identity.',
       'Cross-engine disagreements and conflicting FØR labels remain unresolved. No implied cents.',
       'These are FØR-bearing ad candidates, not all ads in the 29-page flyer.',
       'No HTTP/LLM, new Cloud Vision, database writes or Tjek SKU verification.',
       '', '## Candidate details']
    for r in new['candidates']:
        now=r.get('printed_current_ocr') or {}; cap='; '.join(c['text'][:70] for c in r['nearby_ocr_caption_evidence'][:3])
        report.append(f"p{r['page']:02d} {r['prior']['text']} -> {now.get('value','?')}: {r['status']} ; caption={cap}")
    (args.out_dir/'audit.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    if args.reference:
        with (args.out_dir/'reference-only-review.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=reference_rows[0].keys());writer.writeheader();writer.writerows(reference_rows)
        unresolved=[r for r in reference_rows if r['reference_only_issue']!='strict_reference_overlap_review_only']
        (args.out_dir/'unresolved-reference-review.json').write_text(json.dumps(unresolved,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('W41 A/B V2:',new['counts'])
    print('REFERENCE:',new.get('v2_reference_scoring_only','not supplied'))
    print('Offline review-only output:',args.out_dir)

if __name__=='__main__':main()
