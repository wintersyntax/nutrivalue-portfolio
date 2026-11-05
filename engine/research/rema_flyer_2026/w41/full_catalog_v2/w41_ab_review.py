#!/usr/bin/env python3
"""W41 read-only A/B OCR evidence: detect prior anchors beyond large plaques,
then propose same-region printed-price and caption OCR evidence.

This is NOT an offer-identity verifier. The visual reference is only used in
score(), after all proposals are computed. No API/LLM/database calls.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

WEEK='2026-W41'
PRICE_DEC = re.compile(r'(?<!\d)([1-9]\d{0,2})[,.]([0-9]{2})(?!\d)')
COMPACT = re.compile(r'^([1-9]\d{0,2})(\d{2})$')
PRIOR_MARK = re.compile(r'\b(?:F[ØO0]R|TIDLIGERE)\b',re.I)
EXCLUDE_TEXT = re.compile(r'\b(?:PR\.?\s*(?:KG|L|STK)|PER\s*KG|PANT|L[ÅA]ST\s*PRIS|RABATT|TRUKKET|REMA\s*1000)\b',re.I)
RANGE_RE=re.compile(r'\d\s*[-–]\s*\d')


def asbox(v):
    if not isinstance(v,dict):return None
    try: x,y,w,h=[float(v[k]) for k in ('x','y','width','height')]
    except (KeyError,ValueError,TypeError):return None
    if not all(math.isfinite(n) for n in (x,y,w,h)):return None
    if x<0 or y<0 or w<=0 or h<=0 or x+w>1.003 or y+h>1.003:return None
    return {'x':x,'y':y,'width':w,'height':h}


def center(b):return b['x']+b['width']/2,b['y']+b['height']/2


def amount(s):
    s=unicodedata.normalize('NFKC',str(s)).strip()
    if not re.fullmatch(r'\d{1,3}[.,]\d{2}',s):return None
    v=Decimal(s.replace(',','.'))
    return f'{v:.2f}' if Decimal('.01')<=v<=Decimal('2000') else None


def parse_prior(text):
    s=unicodedata.normalize('NFKC',str(text))
    m=PRIOR_MARK.search(s)
    if not m:return [],'not_prior'
    tail=s[m.end():]
    # 'for' also occurs in ordinary Norwegian prose; a price is required
    # close to the marker even when the digits are not parseable.
    if not re.search(r'\d',tail[:22]):return [],'not_prior'
    values=[amount('.'.join(t)) for t in PRICE_DEC.findall(tail)]
    values=[v for v in values if v]
    if len(values)==2 and RANGE_RE.search(tail):return values,'range'
    if len(values)==1:return values,'single'
    return [],'ambiguous'


def checked_vision(doc):
    if (doc.get('format')!='nutrivalue_ocr_lines_v1' or doc.get('week')!=WEEK or
        doc.get('page_count')!=29 or doc.get('provider')!='google_cloud_vision'):
        raise ValueError('Requires complete Google Vision W41 OCR, not W42/local/Tjek substitute')
    rows=doc.get('pages')
    if not isinstance(rows,list) or len(rows)!=29:raise ValueError('Requires 29 pages')
    for i,row in enumerate(rows,1):
        if row.get('page')!=i or row.get('status')!='processed' or not isinstance(row.get('lines'),list):
            raise ValueError(f'Incomplete or unsorted Vision page {i}')
    return rows


def image_plan(pages,image_dir):
    if not image_dir.is_dir() or image_dir.is_symlink():raise ValueError('Missing images')
    if len(list(image_dir.glob('page-*.jpg')))!=29:raise ValueError('Expected 29 page-XX.jpg images')
    hashes={}
    for p in pages:
        fp=image_dir/f"page-{p['page']:02d}.jpg"
        if not fp.is_file() or fp.is_symlink():raise ValueError('Invalid image file')
        digest=hashlib.sha256(fp.read_bytes()).hexdigest()
        if p.get('image_sha256')!=digest:raise ValueError(f'Vision OCR image hash mismatch on page {p["page"]}')
        hashes[p['page']]=digest
    return hashes


def red_candidates(doc,hashes=None):
    if doc is None:return {}
    if (doc.get('week')!=WEEK or doc.get('page_count')!=29 or
        not isinstance(doc.get('records'),list)):
        raise ValueError('Invalid W41 red-plate OCR')
    out=defaultdict(list)
    for row in doc['records']:
        if hashes is not None and row.get('image_sha256')!=hashes.get(int(row['page'])):
            raise ValueError('Red OCR image hash mismatch')
        b=asbox(row.get('bbox_bottom_origin'))
        if not b:continue
        values=[amount(v) for v in row.get('amounts_nok_review_only',[])]
        values=[v for v in values if v]
        kind='range' if len(values)==2 else 'single' if len(values)==1 else 'ambiguous'
        out[int(row['page'])].append({'page':int(row['page']),'text':str(row.get('text','')),
           'box':b,'amounts':values,'kind':kind,'source':'red_crop_tesseract',
           'review_only':True})
    return out


def detect_priors(lines,page,reds):
    """Geometry-aware de-duplication; never dedup two separate product badges by price alone."""
    proposals=[]
    for idx,ln in enumerate(lines):
        b=asbox(ln.get('box'))
        if not b:continue
        vals,kind=parse_prior(ln.get('text',''))
        if kind=='not_prior':continue
        proposals.append({'page':page,'ocr_line':idx,'text':str(ln['text'])[:180],
              'box':b,'amounts':vals,'kind':kind,'source':'google_vision',
              'review_only':True})
    for r in reds:
        rx,ry=center(r['box'])
        collision=[]
        for p in proposals:
            px,py=center(p['box'])
            # Require same approximate physical marker, not just same price/page.
            if abs(px-rx)<.065 and abs(py-ry)<.04:
                collision.append(p)
        if not collision:
            proposals.append(r)
        elif len(collision)==1:
            q=collision[0]
            # Resolve incomplete Google amounts only if red crop is explicit
            # and does not conflict with an explicit Google amount.
            if q['amounts'] and r['amounts'] and q['amounts']!=r['amounts']:
                q['kind']='conflict';q['amounts']=[]
                q['source']='google_vision+red_crop_disagreement'
            elif not q['amounts'] and r['amounts']:
                q['amounts']=r['amounts'];q['kind']=r['kind']
                q['source']='google_vision+red_crop_rescue'
    return sorted(proposals,key=lambda r:(-center(r['box'])[1],center(r['box'])[0]))


def price_in_line(ln):
    """Printed number only. Large image glyph prerequisite for compact OCR."""
    b=asbox(ln.get('box'));s=unicodedata.normalize('NFKC',str(ln.get('text',''))).strip()
    if b is None or not s or PRIOR_MARK.search(s) or EXCLUDE_TEXT.search(s):return None
    if len(s)>17 or re.search(r'[a-zA-ZÆØÅæøå]',s):return None
    matches=PRICE_DEC.findall(s)
    if len(matches)==1 and re.fullmatch(r'\s*\d{1,3}\s*[,\.]\s*\d{2}\s*',s):
        return amount('.'.join(matches[0]))
    if re.fullmatch(r'\d{1,3}\s+\d{2}',s):
        a,z=s.split();return amount(f'{a}.{z}')
    m=COMPACT.fullmatch(s)
    if m and .028<=b['height']<=.19 and b['width']>=.055:
        return amount(f'{m.group(1)}.{m.group(2)}')
    return None


def regions_for_page(path,source_dir):
    """Reuses THE EXISTING CV function; never reimplements or alters V2."""
    sys.path.insert(0,str(source_dir))
    try:
        from ad_layout_regions import _blue_price_badge_boxes
        return list(_blue_price_badge_boxes(path))
    finally:
        sys.path.pop(0)


def overlap(b,plaque):
    return max(0,min(b['x']+b['width'],plaque['x1'])-max(b['x'],plaque['x0']))*max(
        0,min(b['y']+b['height'],plaque['y1'])-max(b['y'],plaque['y0']))/(b['width']*b['height'])


def near_top(prior,plaque):
    p=prior['box'];px,py=center(p)
    horizontal=max(0,min(p['x']+p['width'],plaque['x1'])-max(p['x'],plaque['x0']))
    return horizontal>=min(.022,p['width']*.20) and -.034<=py-plaque['y1']<=.07


def associate_price(prior,lines,plaques,plaque_cached=None):
    """Price OCR only, bounded to lower/adjacent caption cell; abstain on ties."""
    pb=prior['box'];cx,cy=center(pb)
    matched=[p for p in plaques if near_top(prior,p)]
    matched=matched if len(matched)==1 else []
    # OCR current price can lie below, even when CV missed the small plaque.
    candidates=[]
    for idx,ln in enumerate(lines):
        b=asbox(ln.get('box'))
        if not b:continue
        v=price_in_line(ln)
        if v is None:continue
        x,y=center(b)
        if not(.014<cy-y<.20):continue
        if abs(x-cx)>.195:continue
        if matched and overlap(b,matched[0])<.34:continue
        # Printed current discount generally below the prior badge;
        # use OCR size and geometry, never infer a value from a Tjek price.
        if b['height']<.022:continue
        score=2.0*abs(cx-x)+.7*(cy-y)
        candidates.append((score,idx,v,b))
    candidates.sort()
    # Cached V2 OCR fusion is itself OCR evidence, never a visual label or Tjek price.
    cached=None
    if matched and plaque_cached is not None:
        index=plaques.index(matched[0])
        cached=plaque_cached.get(index)
    if cached and cached.get('status') == 'ocr_disagreement_review_only':
        return {'status':'ocr_disagreement_review_only'},matched
    if cached and cached.get('value'):
        cached_val=amount(cached['value'])
        if cached_val is not None:
            if candidates and any(row[2]!=cached_val and abs(row[0]-candidates[0][0])<.025 for row in candidates):
                return {'status':'ocr_crosscheck_conflict_review_only',
                        'alternatives':sorted({cached_val,candidates[0][2]})},matched
            return {'status':'cached_fused_printed_price_review_only',
                    'value':cached_val,'box':{'x':matched[0]['x0'],'y':matched[0]['y0'],
                    'width':matched[0]['x1']-matched[0]['x0'],
                    'height':matched[0]['y1']-matched[0]['y0']},
                    'source':cached['status']},matched
    if not candidates:return None,matched
    chosen=candidates[0]
    # Reject distinct nearby price proposals rather than selecting by value.
    if len(candidates)>1 and candidates[1][0]-chosen[0]<.025 and candidates[1][2]!=chosen[2]:
        return {'status':'ambiguous_neighbor_prices_review_only','alternatives':[
            {'line':row[1],'value':row[2]} for row in candidates[:4]]},matched
    return {'status':'printed_current_price_candidate_review_only','ocr_line':chosen[1],
            'value':chosen[2],'box':chosen[3],'source':'vision_line_geometry'},matched


def caption_candidates(lines,price_box,all_plaques):
    if not price_box:return []
    px,py=center(price_box)
    neighbors=[]
    for p in all_plaques:
        cx=(p['x0']+p['x1'])/2
        if abs(cx-px)>.1 and abs((p['y1']+p['y0'])/2-py)<.09:
            neighbors.append(cx)
    left=max((px+x)/2 for x in neighbors if x<px) if any(x<px for x in neighbors) else max(0,px-.22)
    right=min((px+x)/2 for x in neighbors if x>px) if any(x>px for x in neighbors) else min(1,px+.22)
    result=[]
    for idx,ln in enumerate(lines):
        b=asbox(ln.get('box'));text=str(ln.get('text','')).strip()
        if not b or not text or len(text)>130 or PRIOR_MARK.search(text):continue
        cx,cy=center(b)
        if not(left<=cx<=right and .015<py-cy<.36):continue
        if not re.search(r'[A-Za-zÆØÅæøå]{3}',text):continue
        if EXCLUDE_TEXT.search(text):continue
        if price_in_line(ln) is not None:continue
        if re.search(r'(?i)\b(?:ALLTID|LAVE PRISER|KUNDEAVIS|PRISEN|BILLIG)\b',text):continue
        # OCR captions with product names are normally multi-letter text in ad lower area.
        result.append({'line_index':idx,'text':text,'box':b,'distance':round(py-cy,5)})
    result.sort(key=lambda x:x['distance'])
    return result[:6]


def checked_plaque_cache(doc):
    """Whitelisted OCR evidence ONLY. Never consume benchmark human label fields."""
    if doc.get('week')!=WEEK or not isinstance(doc.get('plaque_evidence'),list):
        raise ValueError('Expected cached W41 OCR fusion comparison')
    rows=defaultdict(dict)
    for row in doc['plaque_evidence']:
        page=int(row['page']);idx=int(row['blue_plaque_index'])
        if not (1<=page<=29 and 0<=idx<=40) or idx in rows[page]:
            raise ValueError('Invalid/duplicate cached plaque index')
        fusion=row.get('fused',{})
        if not isinstance(fusion,dict):raise ValueError('Invalid fusion row')
        status=fusion.get('status')
        if status not in ('dual_engine_agreement_review_only','vision_only_review_only',
                'tesseract_only_review_only','ocr_disagreement_review_only',
                'unresolved_no_ocr_evidence_review_only'):
            raise ValueError('Unknown fusion status')
        price=amount(fusion.get('value')) if fusion.get('value') else None
        rows[page][idx]={'value':price,'status':status}
    if sum(len(r) for r in rows.values())!=61:raise ValueError('Expected 61 W41 blue-plaque candidates')
    return rows


def analyze(vision,red,image_dir,source_dir,plaque_comparison=None):
    pages=checked_vision(vision)
    hashes=image_plan(pages,image_dir)
    reds=red_candidates(red,hashes)
    cached=checked_plaque_cache(plaque_comparison) if plaque_comparison else {}
    all_items=[];counts=Counter();all_regions=0
    for pg in pages:
        page=pg['page'];lines=pg['lines']
        plaques=regions_for_page(image_dir/f'page-{page:02d}.jpg',source_dir)
        all_regions+=len(plaques)
        priors=detect_priors(lines,page,reds.get(page,[]))
        counts['prior_anchors']+=len(priors)
        for pri in priors:
            price,matched=associate_price(pri,lines,plaques,cached.get(page))
            hits=caption_candidates(lines, price.get('box') if price else None,plaques)
            pbox=price.get('box') if price else None
            status=('ambiguous_prior_review_only' if pri['kind'] in ('ambiguous','conflict') else
                 'ocr_price_caption_candidate_review_only' if price and price.get('value') and hits else
                 'ocr_price_no_caption_review_only' if price and price.get('value') else
                 'ambiguous_price_review_only' if price else 'prior_only_review_only')
            counts[status]+=1
            if not matched:counts['not_matched_to_large_blue_plaque']+=1
            all_items.append({'page':page,'prior':pri,'blue_plaque_match':len(matched)==1,
                'blue_plaque':matched[0] if len(matched)==1 else None,
                'printed_current_ocr':price,'nearby_ocr_caption_evidence':hits,
                'status':status,'tjek_identity_verified':False,
                'printed_prior_price_historically_verified':False,
                'database_writes':False})
    return {'schema':'nutrivalue_w41_ab_evidence_v1','week':WEEK,'pages':29,
      'total_large_blue_plaques':all_regions,'counts':dict(counts),
      'candidates':all_items,'tjek_catalog_linked':False,
      'visual_labels_used_in_inference':False,
      'historical_prices_verified':0,'database_writes':False}


def checked_w41_offers(doc):
    """Optional *normalized* archived W41 Tjek /offers, never W42.

    Offer.page must come from verified W41 page alignment, not guesswork.
    """
    if doc.get('schema')!='nutrivalue_w41_tjek_aligned_offers_v1' or doc.get('week')!=WEEK:
        raise ValueError('W41 normalized Tjek snapshot required; W42 forbidden')
    catalog=doc.get('catalog_id')
    if not isinstance(catalog,str) or not catalog or catalog in ('FClkBYdb','historical-local-2026-W41'):
        raise ValueError('Expected real W41 Tjek catalog_id, not W42 or placeholder')
    offers=doc.get('offers')
    if not isinstance(offers,list) or not offers or len(offers)>1500:raise ValueError('Invalid Tjek offers')
    seen=set();clean=[]
    for row in offers:
        if not isinstance(row,dict) or row.get('catalog_id')!=catalog:raise ValueError('Cross-catalog row')
        page=row.get('page');title=row.get('title');id_=row.get('tjek_offer_id')
        now=amount(row.get('catalog_price_nok'))
        if type(page) is not int or not 1<=page<=29 or not isinstance(title,str) or not title.strip() or not isinstance(id_,str) or not id_ or not now:
            raise ValueError('Missing verified W41 offer title, page, price or ID')
        if id_ in seen:raise ValueError('Duplicate Tjek offer ID')
        seen.add(id_);clean.append({'page':page,'title':title,'id':id_,'price':now})
    return clean


def link_w41_offers_review_only(evidence,offers):
    """Join only if an actual printed OCR price and OCR caption support one W41 offer.

    A Tjek price may corroborate an OCR observation but NEVER replace an unreadable
    printed price. No SKU, checkout-price or old-price verification.
    """
    for candidate in evidence['candidates']:
        candidate['tjek_candidate_review_only']=None
        ocr_price=candidate.get('printed_current_ocr') or {}
        if not ocr_price.get('value') or not candidate['nearby_ocr_caption_evidence']:
            continue
        caption=' '.join(x['text'] for x in candidate['nearby_ocr_caption_evidence'])
        tokens=_tokens(caption)
        matches=[]
        for o in offers:
            if o['page']!=candidate['page'] or o['price']!=ocr_price['value']:continue
            required=_tokens(o['title'])
            if not required:continue
            shared=required & tokens
            if len(shared)<min(2,len(required)) or len(shared)/len(required)<.65:continue
            matches.append(o)
        if len(matches)==1:
            candidate['tjek_candidate_review_only']={
                'tjek_offer_id':matches[0]['id'],'title':matches[0]['title'],
                'price_nok':matches[0]['price'],'page':matches[0]['page'],
                'status':'w41_tjek_ocr_caption_candidate_review_only',
                'verified_product_identity':False}
        elif len(matches)>1:
            candidate['tjek_candidate_review_only']={
                'status':'ambiguous_w41_tjek_candidates_review_only',
                'alternatives':[o['id'] for o in matches],
                'verified_product_identity':False}
    evidence['tjek_catalog_linked']=True
    evidence['tjek_candidates_review_only']=sum(bool(r.get('tjek_candidate_review_only')) for r in evidence['candidates'])
    return evidence


def _tokens(value):
    # Scoring only; visual captions are NEVER used by analyze().
    return {t for t in re.findall(r'[a-z0-9æøå]{3,}',unicodedata.normalize('NFKC',
                str(value)).lower()) if t not in {'rema','1000','stk','for','med','fra','pakke','pris'}}


def score(doc,ref):
    if ref.get('week')!=WEEK or ref.get('record_count')!=39 or len(ref.get('records',[]))!=39:
        raise ValueError('Invalid 39-ad W41 held-aside visual reference')
    # Only amount/page presence, not spatial matching or product verification.
    from collections import Counter
    a=Counter((r['page'],tuple(r['prior']['amounts'])) for r in doc['candidates'] if r['prior']['amounts'])
    b=Counter()
    for r in ref['records']:
        s=r['printed_prior_nok']
        if isinstance(s,list):v=tuple(s)
        elif '-' in s:v=tuple(s.split('-'))
        else:v=(s,)
        b[(r['page'],v)]+=1
    matched=sum(min(a[k],v) for k,v in b.items())
    # Strict held-aside evaluation: same page, same prior amount, same printed
    # current amount AND OCR caption lexical evidence inside our proposed band.
    # This isn't proof of Tjek identity; a caption can cover multiple variants.
    triplet_count=0
    consumed=set()
    for reference in ref['records']:
        old=reference['printed_prior_nok']
        ref_old=tuple(old.split('-')) if isinstance(old,str) and '-' in old else tuple(old) if isinstance(old,list) else (old,)
        wanted=_tokens(reference['visual_product_caption'])
        if not wanted:continue
        for k,candidate in enumerate(doc['candidates']):
            if k in consumed or candidate['page']!=reference['page'] or tuple(candidate['prior']['amounts'])!=ref_old:continue
            price=candidate.get('printed_current_ocr') or {}
            if price.get('value')!=reference['printed_current_nok']:continue
            found=set().union(*(_tokens(hit['text']) for hit in candidate['nearby_ocr_caption_evidence'])) if candidate['nearby_ocr_caption_evidence'] else set()
            if len(wanted & found)>=max(1,min(2,len(wanted))) and len(wanted & found)/len(wanted)>=.50:
                consumed.add(k);triplet_count+=1;break
    return {'reference_ads':39,'prior_amount_present_same_page':matched,
            'prior_amount_missing_or_ambiguous':39-matched,
            'price_caption_proposals_matching_held_aside_reference':triplet_count,
            'not_a_verified_product_identity_measure':True,
            'prior_amount_is_page_only_not_spatial_verified':True,
            'blue_detector_reference_misses':ref.get('known_unseen_by_blue_detector')}

def private_path(path):
    parts=path.resolve().parts
    return any(parts[i:i+2]==('data','private') for i in range(len(parts)-1))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vision',type=Path,required=True)
    p.add_argument('--red',type=Path,required=True)
    p.add_argument('--images',type=Path,required=True)
    p.add_argument('--src',type=Path,required=True)
    p.add_argument('--visual-reference',type=Path)
    p.add_argument('--plaque-comparison',type=Path,help='Cached V2 benchmark OCR fusion; NEVER human labels')
    p.add_argument('--w41-tjek-offers',type=Path,help='Optional verified-week normalized W41 snapshot; not a W42 file')
    p.add_argument('--out-dir',type=Path,required=True)
    args=p.parse_args(argv)
    if not private_path(args.out_dir) or args.out_dir.exists() or args.out_dir.is_symlink():
        p.error('Output must be a new directory under data/private/')
    if not (args.src/'ad_layout_regions.py').is_file():p.error('Expected original V2 source')
    files=[args.vision,args.red]+([args.visual_reference] if args.visual_reference else [])+([args.plaque_comparison] if args.plaque_comparison else [])+([args.w41_tjek_offers] if args.w41_tjek_offers else [])
    if any(not v.is_file() or v.is_symlink() for v in files):p.error('Invalid/symlink input file')
    try:
        doc=analyze(json.loads(args.vision.read_text()),json.loads(args.red.read_text()),args.images,args.src,
            json.loads(args.plaque_comparison.read_text()) if args.plaque_comparison else None)
        if args.w41_tjek_offers:
            link_w41_offers_review_only(doc,checked_w41_offers(json.loads(args.w41_tjek_offers.read_text())))
        if args.visual_reference:
            doc['reference_score_held_aside']=score(doc,json.loads(args.visual_reference.read_text()))
    except (KeyError,ValueError,TypeError,json.JSONDecodeError) as e:p.error('STOP: '+str(e)[:220])
    doc['input_sha256']={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    args.out_dir.mkdir(parents=True)
    (args.out_dir/'w41-ab-evidence.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    lines=['# W41 — OCR-backed ad discovery and caption evidence (review-only)','',
       f"Pages: {doc['pages']}; original V2 large-blue plaques: {doc['total_large_blue_plaques']}",
       f"OCR previous-price anchors: {len(doc['candidates'])}",
       '', '## Counts', *[f'- {k}: {v}' for k,v in sorted(doc['counts'].items())],
       '', '## Reference evaluation — NOT product identity or spatial verification',
       str(doc.get('reference_score_held_aside','No visual reference provided.')),
       '', '## Candidate evidence']
    for it in doc['candidates']:
        pr=it['prior'];current=it['printed_current_ocr'] or {};caps=it['nearby_ocr_caption_evidence']
        lines.append(f"- p{it['page']:02d} {pr['text'][:42]} [{','.join(pr['amounts']) or '?'}] → {current.get('value','?')} | "
                     f"blue={it['blue_plaque_match']} | {it['status']} | captions="+
                     '; '.join(c['text'][:50] for c in caps[:3]))
    lines +=['','## Safety','No W41 Tjek snapshot or only review-only matches: no verified identities.','OCR price/caption pairing is a review hypothesis, not verified product identity.',
        'The 39-ad visual reference is held aside and only used for scoring page/amount appearance.',
        'No HTTP/LLM/cloud OCR or database writes; no historical shelf-price verification.']
    (args.out_dir/'audit.md').write_text('\n'.join(lines)+'\n')
    print('W41 A/B OCR evidence',doc['counts'],'large-blue',doc['total_large_blue_plaques'])
    print('Read-only report:',args.out_dir/'audit.md')

if __name__=='__main__':main()
