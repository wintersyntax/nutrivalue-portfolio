#!/usr/bin/env python3
"""W41 all-pages offline evidence refinement (never verify / publish prices).

Input: V1 candidates, cached W41 Vision OCR, original 29 local page images.
No source-side edits, ground truth, Tjek, LLM, cloud calls, or DB access.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import re
from pathlib import Path
import unicodedata

from w41_ab_review import checked_vision, image_plan, asbox, center
import w41_full_catalog as legacy

NUMBER_ONLY = re.compile(r'^\s*([1-9]\d{0,2})\s*$')
CENTS_ONLY = re.compile(r'^\s*(\d{2})\s*$')
GLUED = re.compile(r'^\s*[A-Za-zÆØÅæøå]{0,2}\s*([1-9]\d{0,2})[ ,.]([0-9]{2})\s*$')
SPLIT_TITLE = re.compile(r'^(.+?)\s+([A-Za-zÆØÅæøå][A-Za-zÆØÅæøå\- ]{1,24})\s*,\s*\d+(?:[,.]\d+)?\s*(?:g|kg|ml|l|stk|pk)\b',re.I)
UNIT_TEXT = re.compile(r'(?i)(?:\bpr\.?\s*(?:kg|l|stk|kilo)|\b\d+(?:[,.]\d+)?\s*(?:g|kg|ml|l|stk|pk)\b)')
DISCOUNT_TEXT = re.compile(r'(?i)\b(?:F[ØO]R|PRISKUTT|RABATT|SPAR|L[ÅA]ST\s+PRIS|BONUS)\b')
PURE_NON_PRODUCT = re.compile(r'(?i)^(?:[+ ]*PANT|PR\s*\.?\s*(?:STK|KG|L)|L[ÅA]ST\s+PRIS|UTEN|NITRITT|LAST|PRIS|REMA\s+1000|NORSK|TIPS!?|GODE\s+HAV|SE ETTER|LINE|VINNERE|PLUKK OG MIKS)$')
MULTI = re.compile(r'(?i)(?:\b[2-9]\s*FOR\b|\bPLUKK\s+OG\s+MIKS\b)')


def fp(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def box_iou(a,b):
    if not a or not b:return 0.
    area=legacy.intersect_area(a,b)
    return area/max(1e-9,a['width']*a['height']+b['width']*b['height']-area)


def box_near(a,b,dx=.053,dy=.053):
    if not a or not b:return False
    ax,ay=center(a);bx,by=center(b)
    return abs(ax-bx)<dx and abs(ay-by)<dy


def minor_price_pair(lines):
    """Conservative price glyphs: two OCR lines, major plus raised smaller 00-99.

    The model must have read *both* sets of digits. Never infer 90/99 cents.
    """
    hits=[]
    for idx,m in enumerate(lines):
        s=str(m.get('text','')).strip(); b=asbox(m.get('box'))
        if not b or not NUMBER_ONLY.fullmatch(s) or b['height']<.030 or b['height']>.11:continue
        major=int(s);mx,my=center(b)
        for j,c in enumerate(lines):
            if j==idx:continue
            cb=asbox(c.get('box'))
            if not cb or not CENTS_ONLY.fullmatch(str(c.get('text','')).strip()):continue
            cents=int(str(c['text']).strip());cx,cy=center(cb)
            if not (.035<=cx-mx<=.13 and -.010<=cy-my<=.085):continue
            if not (.20<=cb['height']/b['height']<=.73):continue
            if cb['width']>=b['width']*1.30:continue
            hits.append({'value':f'{major}.{cents:02d}','box':b,'major_index':idx,'cents_index':j,
                'text':f'{s} + {str(c["text"]).strip()}', 'status':'split_major_cents_spatial_review_only'})
    return hits


def compact_price_glyph(lines):
    """OCR `Q34 90` can be a glyph merge; refuse product/pack-size strings."""
    hits=[]
    for i,l in enumerate(lines):
        s=str(l.get('text','')).strip();b=asbox(l.get('box'))
        m=GLUED.fullmatch(s)
        if not b or not m or b['height']<.025 or b['height']>.11:continue
        if DISCOUNT_TEXT.search(s) or UNIT_TEXT.search(s):continue
        hits.append({'value':f'{int(m.group(1))}.{m.group(2)}','box':b,
                     'ocr_line_indices':[i],'text':s,'status':'compact_glyph_spatial_review_only'})
    return hits


def approx_same_position(a,b):
    return box_near(a,b,.050,.052) or box_iou(a,b)>.18


def attached_to_prior(glyph,existing):
    for r in existing:
        b=r.get('price_box')
        if b and approx_same_position(b,glyph['box']):return True
        b=r.get('prior_box')
        if b:
            x,y=center(glyph['box']);bx,by=center(b)
            if abs(x-bx)<.13 and 0<by-y<.12:return True
    return False


def composite_or_graphic(row,lines):
    price_box=row.get('price_box');prior=row.get('prior_box')
    if row['candidate_source']!='cv_large_blue' or row.get('printed_current_nok'):
        return None
    box=price_box or prior
    if not box:return None
    px,py=center(box)
    nearby=[]
    for l in lines:
        b=asbox(l.get('box'))
        if not b:continue
        x,y=center(b)
        if abs(x-px)<.27 and -.06<=y-py<=.22:
            nearby.append(str(l.get('text','')).upper())
    if any('SLUTTSUMMEN' in s for s in nearby):
        return 'COMPOSITE_MEAL_TOTAL_REVIEW_ONLY'
    # Large decorative logo *contains* the words LÅST PRIS and no price evidence.
    if any(re.search(r'L[ÅA]ST\s+PRIS',s) for s in nearby):
        exact=[]
        for l in lines:
            if re.search(r'(?i)L[ÅA]ST\s+PRIS',str(l.get('text',''))):
                b=asbox(l.get('box'))
                if b and (box_iou(b,box)>.13 or box_near(b,box,.105,.095)):
                    exact.append(l)
        if exact:return 'GRAPHIC_LOCKED_PRICE_LABEL_REVIEW_ONLY'
    return None


def classify_condition(row,lines):
    """Promotion type never automatically changes printed current price."""
    if row.get('filtered_kind')=='COMPOSITE_MEAL_TOTAL_REVIEW_ONLY':return 'COMPOSITE_TOTAL'
    if row.get('filtered_kind')=='GRAPHIC_LOCKED_PRICE_LABEL_REVIEW_ONLY':return 'GRAPHIC_NOT_OFFER'
    box=row.get('price_box')
    if not box:return 'UNKNOWN_PRICE_CONTEXT'
    px,py=center(box)
    selected=[]
    for l in lines:
        b=asbox(l.get('box'))
        if not b:continue
        x,y=center(b)
        if abs(x-px)<.21 and -.09<y-py<.12:selected.append(str(l.get('text','')))
    if any(MULTI.search(s) for s in selected):return 'MULTIBUY_CONDITIONAL'
    if any('SLUTTSUMMEN' in s.upper() for s in selected):return 'COMPOSITE_TOTAL'
    if row.get('printed_current_nok'):return 'INDIVIDUAL_PRINTED_PRICE_CANDIDATE'
    return 'PRICE_UNREADABLE_REVIEW_ONLY'


def clean_title(raw):
    """Display-only trim; avoid turning brand/quantity into a product title."""
    raw=' '.join(str(raw).split())
    if not raw:return ''
    # Price-per-kg can be text following the *product title*, not part of the title.
    bama=re.search(r'(?i)\s+BAMA\s*,\s*\d+(?:[,.]\d+)?\s+pr\s*\.?\s*kg\b',raw)
    if bama and bama.start()>3:return raw[:bama.start()].strip(' ,-')
    # Explicit brand-and-package suffixes, not an unbounded arbitrary word split.
    m=re.search(r'(?i)\s+(?:Gode\s+hav|Kolonihagen|Nordfjord|Solvinge|Bama|REMA\s+1000|Gilde|Mesterbakeren|Toro|Norgesmøllene|Freia|Hatting|Stange|Dr\.?\s*Oetker|Lille\s+skinkefabrikk|Taga|Kims|Synnøve|Dansukker|Tine)\s*,\s*\d+(?:[,.]\d+)?\s*(?:g|kg|ml|l|stk|pk)\b',raw)
    if m and m.start()>=4:return raw[:m.start()].strip(' ,-')
    # For candidates without a recognized brand: pure BAMA/Gode-hav + weight
    # is metadata, not a product title.
    if re.match(r'(?i)^(?:Gode\s+hav|Kolonihagen|Nordfjord|Solvinge|Bama|REMA\s+1000|Gilde|Mesterbakeren|Toro|Norgesmøllene|Freia|Hatting|Stange|Tine|Dansukker)\s*,\s*\d+',raw):
        return raw
    # Recognizable title followed by a short supplier then mass.
    m=SPLIT_TITLE.match(raw)
    if m and len(m.group(1))>4 and m.group(1).upper() not in ('GODE','REMA','BAMA','TORO'):
        return m.group(1).strip(' ,-')
    return raw[:160]


def caption_peer_conflict(text_box, price_box, anchors):
    """Veto when another price spatially owns the would-be product label."""
    tx,ty=center(text_box);px,py=center(price_box)
    dx=abs(tx-px);dy=abs(ty-py)
    for a in anchors:
        if a is price_box or approx_same_position(a,price_box):continue
        ax,ay=center(a)
        adx=abs(ax-tx);ady=abs(ay-ty)
        if adx<.22 and ady<.075 and dy>.105 and ady+.065<dy:
            return True
        if adx+.060<dx and ady<=dy+.015 and adx<.20 and ady<.10:
            return True
    return False


def title_score(text, price_box, text_box, anchors):
    raw=' '.join(str(text).strip().split());s=clean_title(raw)
    if not s or PURE_NON_PRODUCT.fullmatch(s) or DISCOUNT_TEXT.search(raw):return -1e6
    if re.search(r'(?i)\b(?:ALLTID\s+10|BONUS|VINNERE|LOTTERI|PRISENE\s+KAN|MÅ\s+VINNE)\b',raw):return -1e6
    if re.search(r'(?i)(?:\+\s*PANT|\bSLUTTSUMMEN\b)',raw):return -1e6
    if re.search(r'(?i)\bPR\s*\.\s*(?:STK|KG|L)\b',raw) and clean_title(raw)==raw:return -1e6
    if len(s)<4 or not re.search('[A-Za-zÆØÅæøå]{3}',s):return -1e6
    ux,uy=center(text_box)
    px,py=center(price_box)
    dy=py-uy
    if not (.008<dy<.37):return -1e6
    dx=abs(px-ux)
    if dx>.345:return -1e6
    # A separate price marker nearer to the caption should usually own it.
    if caption_peer_conflict(text_box,price_box,anchors):return -1e6
    for anchor in anchors:
        if anchor is price_box or approx_same_position(anchor,price_box):continue
        ax,ay=center(anchor)
        if (abs(ax-ux)+.017 < dx and
            (0 < ay-uy < .35 and abs(ay-py)<.18 or
             abs(ay-uy)<.065 and abs(ax-ux)<.16 and abs(ay-py)<.23)):
            return -1e6
    score=7.5 - dx*8 - abs(dy-.105)*8
    if UNIT_TEXT.search(raw):
        # A caption line may contain a product heading AND a pack/unit-rate tail.
        score-=1.1 if SPLIT_TITLE.match(raw) or clean_title(raw)!=raw else 5.6
    if re.search(r'(?i)\b(?:PR\.?\s*KG|PR\.?\s*L|PR\.?\s*STK)\b',raw):score-=1.5
    if re.search(r'\d',s):score-=.8
    letters=[ch for ch in s if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters)/len(letters)>.68:score+=2.7
    if len(s.split())>=2:score+=.6
    if len(s)>=10:score+=.5
    if len(s)<8 and s.isupper() and len(s.split())==1:score-=.6
    if re.search(r'(?i)\b(?:6PK|2PK|3PK|4PK)\b',s):score+=.7
    if len(s.split())>8:score-=1.8
    if not UNIT_TEXT.search(raw) and s==s.upper():score+=.4
    if re.search(r'(?i)^(?:BAMA|KOLONIHAGEN|GODE HAV|REMA 1000|SOLVINGE|TORO|NORD FJORD)\b',s) and len(s.split())<4:score-=2.2
    return round(score,3)


def title_options(row,lines,anchors):
    """Rank V1 cell-filtered captions first; do not roam into neighboring ads.

    Page-wide OCR is fallback only when the original cell has no plausible
    product title. Human review still required for every title.
    """
    price_box=row.get('price_box') or row.get('prior_box')
    if not price_box:return [],''
    options=[];seen=set()

    def collect(raw,box,index,source):
        if not box:return
        key=(str(raw),round(box['x'],4),round(box['y'],4))
        if key in seen:return
        seen.add(key)
        score=title_score(raw,price_box,box,anchors)
        if score<-50:return
        if source=='vision_fallback':score-=1.5
        options.append({'text':str(raw)[:180],'clean_title':clean_title(raw),
                        'box':box,'source':source,'ocr_line_index':index,'score':round(score,3)})

    for c in row.get('caption_candidates',[]):
        collect(c.get('text',''),asbox(c.get('box')),c.get('ocr_line_index'),'v1_spatial_cell')

    plausible=[o for o in options if o['score']>=4.0 and not UNIT_TEXT.search(o['clean_title'])]
    if not plausible:
        px,py=center(price_box)
        for i,l in enumerate(lines):
            box=asbox(l.get('box'))
            if not box:continue
            x,y=center(box)
            if not (.009<py-y<.29) or abs(px-x)>.345:continue
            if box['height']>.080:continue
            collect(l.get('text',''),box,i,'vision_fallback')

    options.sort(key=lambda o:(-o['score'],o['source']!='v1_spatial_cell',o['text']))
    # If a caption looks suspicious, preserve V1 proposal; do not guess from a
    # lower scoring neighboring OCR line.
    chosen=options[0]['clean_title'] if options and options[0]['score']>=3.7 else ''
    return options[:8],chosen


def unit_price_context(row,lines):
    """Optional adjacent per-kg evidence. NEVER overrides the printed OCR price."""
    if row.get('pricing_mode')=='MULTIBUY_CONDITIONAL':return None
    if 'PRICE_PER_KG_BADGE_REVIEW_ONLY' not in row.get('promotion_flags',[]):return None
    title_options=row.get('title_evidence_v2',[])
    if not title_options or not row.get('printed_current_nok'):return None
    active_title=next((t for t in title_options if t.get('clean_title')==row.get('proposed_product_text_v2')),None)
    if active_title is None:return None
    tx,ty=center(active_title['box'])
    for i,l in enumerate(lines):
        b=asbox(l.get('box'));raw=str(l.get('text',''))
        if not b:continue
        x,y=center(b)
        if abs(x-tx)>.16 or not (-.075 < y-ty < .035):continue
        match=re.search(r'(?i)(?<!\d)(\d{1,3})[,.](\d{2})\s+pr\s*\.?\s*(?:kg|kilo|l)\b',raw)
        if not match:continue
        # This may be a unit-price rate, not the shelf or ad's unit price.
        observation=f'{int(match.group(1))}.{match.group(2)}'
        # Packed quantities make the shelf price and unit rate incomparable.
        prefix=raw[:match.start()]
        packaged=bool(re.search(r'(?i)\b\d+(?:[,.]\d+)?\s*(?:g|kg|ml|l|stk|pk)\b',prefix))
        return {'value_nok':observation,'line_index':i,'raw_text':raw[:180],
            'status':'ADJACENT_UNIT_RATE_REVIEW_ONLY',
            'comparable_per_weight':not packaged,
            'differs_from_printed_ocr':not packaged and observation!=row['printed_current_nok']}
    return None


def attach_glyph(glyph,records, source):
    """Spatially match glyph to an existing candidate, else create one.

    Equal prices at distinct positions are explicitly NOT duplicate claims.
    """
    gb=glyph['box']
    same=[r for r in records if r.get('price_box') and approx_same_position(r['price_box'],gb)]
    if len(same)==1:
        r=same[0];old=r.get('printed_current_nok')
        if not old:
            r['printed_current_nok']=glyph['value']
            r['price_box']=gb
            r['price_reading_status']='new_split_or_glued_ocr_review_only'
            r['price_evidence'].append(glyph)
        elif old!=glyph['value']:
            r['price_evidence'].append(glyph)
            r['price_disagreement']=True
            r['price_reading_status']='OCR_PRICE_CONFLICT_REVIEW_ONLY'
        return 'enriched'
    if same:return 'ambiguous'
    if attached_to_prior(glyph,records):return 'belongs_to_prior'
    record={'candidate_id':'','page':None,'candidate_source':source,
            'printed_current_nok':glyph['value'],'prior_nok':[], 'prior_text':'',
            'price_box':gb,'prior_box':None,'caption_candidates':[],
            'evidence_status':glyph['status'],'source_evidence':glyph['text'],
            'needs_review':True,'tjek_identity_verified':False,'promotion_flags':[],
            'review_only':True,'price_evidence':[glyph], 'price_reading_status':'new_split_or_glued_ocr_review_only'}
    records.append(record)
    return 'new'


def add_partial_price_anchors(lines,records):
    """Detect incomplete *large* price glyphs for review, do not invent cents.

    Requires a nearby product label below the fragment. Not enough to assert a price.
    """
    found=[]
    for i,l in enumerate(lines):
        txt=str(l.get('text','')).strip();b=asbox(l.get('box'))
        if not b or not (.031<b['height']<.105):continue
        major=None
        if NUMBER_ONLY.fullmatch(txt):major=txt
        else:
            m=re.fullmatch(r'(?i)([A-ZÆØÅ]{2,8})\s+([1-9]\d{1,2})',txt)
            if m:major=m.group(2)
        if not major:continue
        if any(approx_same_position(b,(r.get('price_box') or r.get('prior_box'))) for r in records if (r.get('price_box') or r.get('prior_box'))):continue
        x,y=center(b)
        if not any((tb:=asbox(z.get('box'))) and .02<y-center(tb)[1]<.16 and abs(x-center(tb)[0])<.19
                   and re.search(r'[A-Za-zÆØÅæøå]{4}',str(z.get('text','')))
                   and not re.search(r'(?i)(?:PLUKK|MIKS|VINNER|BONUS|SE ETTER|RABATT|F[ØO]R)',str(z.get('text',''))) for z in lines):continue
        if any((zb:=asbox(z.get('box'))) and abs(center(zb)[0]-x)<.35 and abs(center(zb)[1]-y)<.20
                  and re.search(r'(?i)(?:PLUKK|MIKS|[2-9]\s*FOR\s*[2-9]|BONUS|VINNER)',str(z.get('text',''))) for z in lines):continue
        found.append({'ocr_line_index':i,'text':txt,'box':b,'read_major_only':major})
    # A partial price is evidence only; avoid unbounded speculative additions.
    for h in found[:2]:
        records.append({'candidate_id':'','page':None,'candidate_source':'vision_partial_price_fragment',
           'printed_current_nok':None,'prior_nok':[],'prior_text':'','price_box':h['box'],'prior_box':None,
           'caption_candidates':[],'evidence_status':'cents_missing_review_only',
           'source_evidence':h,'needs_review':True,'review_only':True,'tjek_identity_verified':False,
           'promotion_flags':[], 'price_evidence':[h], 'price_reading_status':'MAJOR_ONLY_REVIEW_ONLY'})
    return len(found[:2])


def refine(old,vision):
    pages=checked_vision(vision)
    if old.get('week')!='2026-W41' or old.get('pages')!=29 or not old.get('review_only') or old.get('candidate_count')!=len(old.get('candidates',[])):
        raise ValueError('Expected complete review-only W41 V1 candidates')
    for r in old['candidates']:
        if r.get('tjek_identity_verified') is not False or r.get('review_only') is not True:raise ValueError('Unsafe previously verified W41 row')
    bypage=defaultdict(list)
    for r in old['candidates']:
        row=json.loads(json.dumps(r))
        row['v1_candidate_id']=r['candidate_id'];row['price_evidence']=[]
        row['price_disagreement']=False
        row['price_reading_status']='original_ocr_review_only'
        bypage[row['page']].append(row)
    diagnostics=Counter()
    for p in pages:
        page=p['page']; lines=p['lines']; records=bypage[page]
        for r in records:
            r['filtered_kind']=composite_or_graphic(r,lines)
            if r['filtered_kind']:diagnostics['non_item_regions_flagged']+=1
        for g in minor_price_pair(lines):
            result=attach_glyph(g,records,'vision_split_price')
            diagnostics['split_'+result]+=1
        for g in compact_price_glyph(lines):
            result=attach_glyph(g,records,'vision_glued_price')
            diagnostics['glued_'+result]+=1
        add_partial_price_anchors(lines,records)
        anchors=[r.get('price_box') for r in records if r.get('price_box') and not r.get('filtered_kind')]
        for r in records:
            r['page']=page
            r['pricing_mode']=classify_condition(r,lines)
            if r['pricing_mode']=='MULTIBUY_CONDITIONAL':
                r['campaign_product_scope']='GROUP_OR_MULTIBUY_REVIEW_ONLY_NOT_UNIT_PRICE'
            if r.get('filtered_kind'):
                r['proposed_product_text_v2']=''
                r['title_evidence_v2']=[]
                continue
            opts,name=title_options(r,lines,anchors)
            r['title_evidence_v2']=opts
            previous=r.get('proposed_product_text','')
            old_clean=clean_title(previous)
            # A distinctive V1 product name is preserved unless new evidence
            # is lexically compatible. Corrections do not silently swap SKUs.
            old_descriptor=bool((re.search(r'(?i)\bpr\s*\.?\s*(?:kg|l|stk|i|1)\b',previous)
                or re.match(r'(?i)^(?:Gode hav|Kolonihagen|Stange|Toro|Bama|Synnøve|Nordfjord|Solvinge|Norgesmøllene|Dansukker)\s*,\s*\d+',previous))
                and old_clean==previous)
            old_title=bool(old_clean and not old_descriptor and not PURE_NON_PRODUCT.fullmatch(old_clean))
            normalize=lambda v:re.sub(r'[^A-Z0-9]','',unicodedata.normalize('NFKC',v).upper())
            old_box=next((asbox(z.get('box')) for z in r.get('caption_candidates',[])
                         if z.get('text')==previous and asbox(z.get('box'))),None)
            crossed=bool(old_box and caption_peer_conflict(old_box,(r.get('price_box') or r.get('prior_box')),anchors))
            if old_title and not crossed and (not name or (normalize(old_clean) not in normalize(name)
                      and normalize(name) not in normalize(old_clean))):
                r['title_selection_note']='V1_TITLE_RETAINED_UNCERTAIN_NEW_ALTERNATIVE'
                name=old_clean
            elif crossed:
                r['title_selection_note']='V1_CAPTION_SPATIALLY_OWNED_BY_NEIGHBOR_REVIEW_ONLY'
            elif name!=old_clean:
                r['title_selection_note']='SPATIAL_OCR_ALTERNATIVE_REVIEW_ONLY'
            else:
                r['title_selection_note']='ORIGINAL_CAPTION_REUSED_REVIEW_ONLY'
            r['proposed_product_text_v2']=name
            r['nearby_unit_rate_evidence']=unit_price_context(r,lines)
            if r['nearby_unit_rate_evidence'] and r['nearby_unit_rate_evidence']['differs_from_printed_ocr']:
                r['price_context_warning']='OCR_UNIT_RATE_DISAGREES_REVIEW_ONLY'
            r['product_identity_status']='ocr_caption_spatial_candidate_review_only' if name else 'no_unique_product_title_review_only'
            r['tjek_identity_verified']=False
            r['review_only']=True
        # Preserve identifiers of all original records; new records use reserved IDs.
        new_idx=1
        for r in records:
            if not r['candidate_id']:
                r['candidate_id']=f'W41-P{page:02d}-N{new_idx:03d}'
                new_idx+=1
    candidates=[r for page in range(1,30) for r in bypage[page]]
    return {'schema':'nutrivalue_w41_full_catalog_candidates_v2_review_only', 'week':'2026-W41','pages':29,
            'campaign_text_signals':old.get('campaign_text_signals',[]),
            'candidates':candidates,'candidate_count':len(candidates),'base_candidate_count':len(old['candidates']),
            'diagnostics':dict(diagnostics),'manual_full_catalog_reference_complete':False,
            'full_catalog_recall':None,'correct_product_price_recall':None,
            'tjek_identity_verified':False,'data_writes':False,'review_only':True}


def write_visual_review(doc,out):
    import html
    bypage=defaultdict(list)
    for r in doc['candidates']:bypage[r['page']].append(r)
    lines=['<!doctype html><html lang="hr"><meta charset="utf-8"><title>W41 full catalog V2 review</title>',
      '<style>body{font:14px system-ui;margin:1.6rem auto;max-width:1100px;color:#222}section{border-top:1px solid #bbb;padding:1rem 0}img{max-width:100%;max-height:950px}td,th{border:1px solid #bbb;padding:5px;vertical-align:top}table{border-collapse:collapse;width:100%}summary{cursor:pointer;padding:.45rem}small{color:#666}</style>',
      '<h1>W41 — Detekcija i nazivi V2 (REVIEW ONLY)</h1>',
      '<p>Svi prikazani nazivi i cijene su OCR prijedlozi. Filter za SLUTTSUMMEN/LÅST PRIS i nepotpune cijene treba provjeriti na slikama. Bez pregleda svih 29 stranica nema postotka potpune pokrivenosti.</p>']
    for p in range(1,30):
        lines += [f'<section><details><summary><strong>Stranica {p:02d}</strong> — {len(bypage[p])} kandidata</summary>',
                  f'<img loading="lazy" src="overlays/page-{p:02d}.jpg" alt="Page {p}">',
                  '<table><tr><th>ID</th><th>Cijena</th><th>Novi naslov</th><th>Prije</th><th>Tip</th><th>Upozorenja</th></tr>']
        for r in bypage[p]:
            safe=lambda v:html.escape(str(v or ''))
            x=[r['candidate_id'],r.get('printed_current_nok'),r.get('proposed_product_text_v2'),r.get('proposed_product_text'),
               r.get('pricing_mode'),r.get('filtered_kind') or r.get('price_context_warning') or r.get('title_selection_note')]
            lines.append('<tr>'+''.join('<td>'+safe(z)+'</td>' for z in x)+'</tr>')
        lines += ['</table></details></section>']
    lines.append('</html>')
    (out/'review-v2.html').write_text('\n'.join(lines),encoding='utf-8')


def write_result(doc, out, images):
    out.mkdir(parents=True,exist_ok=False)
    (out/'candidates-v2.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    projection=json.loads(json.dumps(doc,ensure_ascii=False))
    for r in projection['candidates']:
        r['proposed_product_text']=r.get('proposed_product_text_v2','')
    (out/'candidates.json').write_text(json.dumps(projection,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    legacy.make_review_files(projection,out)
    cols=['candidate_id','page','candidate_source','pricing_mode','printed_current_nok','proposed_product_text_v2','proposed_product_text','price_reading_status','filtered_kind','review_status']
    with (out/'candidate-changes.csv').open('w',newline='',encoding='utf-8-sig') as f:
        wr=csv.DictWriter(f,fieldnames=cols);wr.writeheader()
        for r in doc['candidates']:
            wr.writerow({k:(r.get(k) or '') if k!='review_status' else 'REVIEW_ONLY' for k in cols})
    legacy.draw_overlays(projection,images,out)
    write_visual_review(projection,out)
    counts=Counter(r['pricing_mode'] for r in doc['candidates'])
    no_title=sum(not r.get('proposed_product_text_v2') and not r.get('filtered_kind') for r in doc['candidates'])
    changes=sum((r.get('proposed_product_text') or '')!=(r.get('proposed_product_text_v2') or '') for r in doc['candidates'] if not r.get('filtered_kind'))
    report=['# W41 Full Catalog V2 — offline candidate refinement (REVIEW ONLY)','',
      f'V1 candidates: {doc["base_candidate_count"]}; V2 candidate hypotheses: {doc["candidate_count"]}.',
      f'Proposed product titles changed: {changes}; missing title proposals after V2: {no_title}.',
      '## Price/campaign types',*[f'- {k}: {v}' for k,v in sorted(counts.items())],
      '## Diagnostics',*[f'- {k}: {v}' for k,v in sorted(doc['diagnostics'].items())],
      f'OCR campaign text signals retained: {len(doc.get("campaign_text_signals",[]))} (NOT distinct campaigns).',
      '', 'No full-catalog recall metric yet: independent review of all 29 pages is required.',
      'All outputs are review-only: no Tjek identity, no confirmed former prices, no DB writes.',
      'No network, Google/Paddle/LLM/API calls. Additional OCR price fragments are NOT accepted prices.']
    (out/'audit.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    return '\n'.join(report)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    for a in ['vision','v1','images','out-dir']:ap.add_argument('--'+a,type=Path,required=True)
    a=ap.parse_args(argv)
    for p in [a.vision,a.v1]:
        if not p.is_file() or p.is_symlink():ap.error(f'Missing or symlinked input: {p}')
    if a.out_dir.exists() or not str(a.out_dir).startswith('data/private/'):
        ap.error('Output must be a NEW path under data/private/')
    vision=json.loads(a.vision.read_text(encoding='utf-8'))
    old=json.loads(a.v1.read_text(encoding='utf-8'))
    if old.get('evidence_sha256',{}).get('vision')!=fp(a.vision):
        ap.error('FAIL-CLOSED: cached Vision SHA differs from V1 provenance')
    image_plan(checked_vision(vision),a.images)
    doc=refine(old,vision)
    doc['input_sha256']={'vision':fp(a.vision),'v1':fp(a.v1)}
    print(write_result(doc,a.out_dir,a.images))

if __name__=='__main__':main()
