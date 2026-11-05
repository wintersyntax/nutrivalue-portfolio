"""Read-only, conservative 2-D ad-region hypotheses for OCR old/new prices.

OCR geometry is normalized in the *stored NutriValue schema*, with y increasing
upwards.  The segmentation uses FØR price-badge anchors; it is a proposed
layout partition, NOT pixel-verified physical ad segmentation.  OpenCV measures
possible gutters when a local image is available; it never fabricates joins.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from decimal import Decimal
from pathlib import Path
from typing import Any
import math
import re
import copy
import unicodedata
from difflib import SequenceMatcher

import cv2
import numpy as np
from shapely.geometry import box as shapely_box

from api_first_audit import money, ocr_current_amount
from signal_semantics_v12 import _prior_money, norm_tokens


def _valid_box(b):
    if not isinstance(b, dict):
        return False
    try:
        x,y,w,h = (float(b[k]) for k in ('x','y','width','height'))
        return all(math.isfinite(v) for v in (x,y,w,h)) and 0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1.002 and y+h <= 1.002
    except (TypeError, ValueError, KeyError):
        return False


def _center(b):
    return (float(b['x'])+float(b['width'])/2, float(b['y'])+float(b['height'])/2)


def _region_contains(b, region, margin=0):
    if not _valid_box(b): return False
    x,y=_center(b)
    return (region['x0']-margin <= x <= region['x1']+margin
            and region['y0']-margin <= y <= region['y1']+margin)


def _overlap_ratio(b, region):
    if not _valid_box(b): return 0
    r=shapely_box(region['x0'],region['y0'],region['x1'],region['y1'])
    o=shapely_box(b['x'],b['y'],b['x']+b['width'],b['y']+b['height'])
    return r.intersection(o).area / o.area


def page_regions(prior_signals):
    """Create 2D price-badge-seeded regions by clustering x into ad columns,
    then segmenting each column at midpoints between neighboring FØR badges.

    This works even when product pictures cross the inferred grid; untrusted
    overlapping content then remains review-only rather than forcibly linked.
    """
    seeds=[s for s in prior_signals if s.get('kind')=='prior_price' and _valid_box(s.get('box'))]
    if not seeds:return {}
    seeds=sorted(seeds,key=lambda s:_center(s['box'])[0])
    columns=[]
    for seed in seeds:
        x,_=_center(seed['box'])
        if columns and x - float(np.median([_center(s['box'])[0] for s in columns[-1]])) < .19:
            columns[-1].append(seed)
        else:
            columns.append([seed])
    cxs=[float(np.median([_center(s['box'])[0] for s in col])) for col in columns]
    result={}
    for ci,col in enumerate(columns):
        x0=0.0 if ci==0 else (cxs[ci-1]+cxs[ci])/2
        x1=1.0 if ci==len(columns)-1 else (cxs[ci]+cxs[ci+1])/2
        # Wide single-column layouts may occupy the full width; no other
        # columns means we must not invent vertical dividers.
        col=sorted(col,key=lambda s:_center(s['box'])[1])
        ys=[_center(s['box'])[1] for s in col]
        for idx,s in enumerate(col):
            y0=0 if idx==0 else (ys[idx-1]+ys[idx])/2
            y1=1 if idx==len(col)-1 else (ys[idx]+ys[idx+1])/2
            result[s['signal_id']]={'x0':round(x0,6),'x1':round(x1,6),
                     'y0':round(y0,6),'y1':round(y1,6),
                     'status':'ocr_price_grid_hypothesis_review_only',
                     'column':ci,'row_from_bottom':idx,'neighbor_prior_count':len(col)}
    return result


@lru_cache(maxsize=4)
def _image_features(path):
    im=cv2.imread(str(path),cv2.IMREAD_COLOR)
    if im is None:return None
    h,w=im.shape[:2]
    if h < 300 or w < 200:return None
    # Mixed banner/body flyer pages have different corner colors (e.g. a
    # green header and blue product field). Four-corner consensus falsely
    # disables CV on such pages. Use the modal flat field *only if* it has
    # substantial support. A photographic full-bleed page still abstains.
    stride=max(3,min(h,w)//125)
    sampled=im[::stride,::stride].reshape(-1,3).astype(np.int16)
    quantized=(sampled//16).astype(np.uint8)
    _, inverse, counts=np.unique(quantized,axis=0,return_inverse=True,return_counts=True)
    winner=int(np.argmax(counts))
    pixels=sampled[inverse==winner]
    background=np.median(pixels,axis=0)
    support=float((np.max(np.abs(sampled-background),axis=1)<25).mean())
    if support<.20:return None
    edges=cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),(5,5),0),65,125)
    return im,edges,background


def refine_regions_with_cv(regions,priors,image_path):
    """Adjust tentative vertical/horizontal Voronoi dividers only when a
    measurable blank *background-colored* seam separates two badge clusters.
    Otherwise retain the geometric hypothesis and mark it as unverified.
    """
    if not image_path or not Path(image_path).is_file():return regions
    features=_image_features(str(image_path))
    if features is None:return regions
    im,edges,bg=features
    h,w=im.shape[:2]
    byid={s['signal_id']:s for s in priors if s['signal_id'] in regions}
    column_groups=defaultdict(list)
    for sid,r in regions.items():column_groups[r['column']].append(sid)
    columns=sorted(column_groups)
    for a,b in zip(columns,columns[1:]):
        one=column_groups[a];two=column_groups[b]
        xs1=[_center(byid[s]['box'])[0] for s in one]
        xs2=[_center(byid[s]['box'])[0] for s in two]
        if not xs1 or not xs2:continue
        guess=regions[one[0]]['x1']
        # Confine to the vertical portion of the flyer containing the badges
        # and product titles below them. Whole-page whitespace can mislead.
        yvals=[_center(byid[s]['box'])[1] for s in one+two]
        bottom_y=max(0,min(yvals)-.19)
        top_y=min(1,max(yvals)+.015)
        start=int((1-top_y)*h);end=int((1-bottom_y)*h)
        if end-start<60:continue
        valid=[]
        for x in np.arange(max(max(xs1)+.07,guess-.105),
                           min(min(xs2)-.07,guess+.105),.005):
            px=int(float(x)*w)
            mask=im[start:end,max(px-4,0):min(w,px+5)]
            edge=edges[start:end,max(px-4,0):min(w,px+5)]
            if mask.size==0:continue
            bg_fraction=float((np.max(np.abs(mask.astype(np.int16)-bg),axis=2)<24).mean())
            ed=float((edge>0).mean())
            if bg_fraction>.91 and ed<.022:
                valid.append((float(x),bg_fraction,ed))
        if not valid:continue
        # Favor wide central blank runs, but never move the midpoint farther
        # than a tenth of the whole page based on low-value pixel evidence.
        best=min(valid,key=lambda v:(-v[1],v[2],abs(v[0]-guess)))
        boundary=round(best[0],6)
        for sid in one:
            regions[sid]['x1']=boundary
            regions[sid]['status']='ocr_grid_with_cv_vertical_seam_review_only'
            regions[sid]['cv_separator']={'x':boundary,'background_fraction':round(best[1],3),
                                          'edge_fraction':round(best[2],4)}
        for sid in two:
            regions[sid]['x0']=boundary
            regions[sid]['status']='ocr_grid_with_cv_vertical_seam_review_only'
            regions[sid]['cv_separator']={'x':boundary,'background_fraction':round(best[1],3),
                                          'edge_fraction':round(best[2],4)}
    return regions


def current_price_observations(page, valid_tjek_prices=None):
    """Accept fully read OCR current prices and narrowly constrained badge
    OCR failures (e.g. large '19' beside small '90'). Never use a Tjek value
    as standalone evidence of a printed price.
    """
    out=[]
    lines=page.get('lines',[])
    for i,l in enumerate(lines):
        if not _valid_box(l.get('box')):continue
        label=str(l.get('text','')).strip()
        if re.match(r'^(?:F[ØO]R|TIDLIGERE)(?:\b|\s)',label,re.I):
            continue   # An old-price label is NEVER a current-price anchor.
        amount=ocr_current_amount(label)
        if amount is not None and (valid_tjek_prices is None or amount in valid_tjek_prices):
            out.append({'amount':amount,'line_indices':[i],'boxes':[l['box']],
                        'source':'literal_ocr_current_price'})
    # Some badges are read as a compact 1990 without a decimal point.
    for i,l in enumerate(lines):
        if not _valid_box(l.get('box')): continue
        text=str(l.get('text','')).strip()
        normalized = unicodedata.normalize('NFKC', text)
        # A common Vision error in large price badges is a raised øre digit:
        # 55⁹0 or 55⁹⁰ means 55.90. NFKC folds superscripts to digits.
        # Never interpret a prior marker or arbitrary free text as current NOK.
        raised_cents = bool(re.fullmatch(r'\d{1,3}[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,2}\d?', text))
        compact = bool(re.fullmatch(r'\d{3,4}', text))
        if (compact or raised_cents) and float(l['box']['height'])>=.024:
            amount=Decimal(int(normalized))/100
            if valid_tjek_prices is None or amount in valid_tjek_prices:
                out.append({'amount':amount,'line_indices':[i],'boxes':[l['box']],
                            'source':('raised_ore_compact_price_ocr_review_only' if raised_cents
                                      else 'large_compact_price_ocr_review_only')})
    # Split badge: high-size kronor and small superscript øre, on same printed
    # badge within 0.14 of page width. Only consider values matching Tjek.
    for i,whole in enumerate(lines):
        if not _valid_box(whole.get('box')):continue
        t=str(whole.get('text','')).strip()
        if not re.fullmatch(r'\d{1,3}',t):continue
        b=whole['box']; xc,yc=_center(b)
        if float(b['height'])<.027:continue
        for j,cents in enumerate(lines):
            if j==i or not _valid_box(cents.get('box')):continue
            ct=str(cents.get('text','')).strip()
            if not re.fullmatch(r'\d{2}',ct): continue
            cb=cents['box']; cx,cy=_center(cb)
            if not (0 < cx-xc <= .145 and abs(cy-yc)<=.085):continue
            if float(cb['height'])>float(b['height'])*1.12:continue
            amount=Decimal(f'{int(t)}.{ct}')
            if valid_tjek_prices is None or amount in valid_tjek_prices:
                out.append({'amount':amount,'line_indices':[i,j],
                            'boxes':[b,cb],'source':'split_price_badge_review_only'})
    # Preserve source lines; deduplicate multiple detection modes for same box.
    return list({(tuple(x['line_indices']),x['amount']):x for x in out}.values())


def _product_match(ln_text,title):
    required=set(norm_tokens(title))
    actual=set(norm_tokens(ln_text))
    common=required & actual
    if not required or not actual:return False
    if len(required)==1:
        token=next(iter(required))
        if len(token)<5:return False
        if token in actual:return True
        # A single OCR letter dropped inside a DISTINCTIVE product name is
        # common. The fuzzy backup is restricted to a verified price cell;
        # it cannot override the exact Tjek current-price match.
        return len(token)>=9 and any(
            abs(len(token)-len(w))<=2 and
            SequenceMatcher(None, token, w).ratio()>=.89
            for w in actual if len(w)>=8)
    return len(common)>=2 and len(common)/len(required)>=.6



def _compact_ocr_title(value):
    """OCR comparison only: tolerate spaces/hyphens *inside* a printed name.

    This is not a fuzzy semantic product match and cannot supply missing text.
    The normalized title must be completely present in one or two real OCR
    lines in the same candidate cell (never inferred from an API price).
    """
    return ''.join(c for c in unicodedata.normalize('NFKC', str(value)).upper()
                   if c.isalnum())


def _recovery_title_hits(page, title, prior, region):
    """Return conservative, coordinate-backed printed title candidates.

    The existing matcher takes precedence. The recovery-only alternatives are
    an exact dehyphenated full OCR line, or two tightly adjacent OCR lines
    spelling the ENTIRE API title. Never join across a cell border.
    """
    target=_compact_ocr_title(title)
    hits=[]
    if len(target)<9:
        return hits
    lines=page.get('lines',[])
    required=set(norm_tokens(title))
    eligible=[]
    for i,line in enumerate(lines):
        b=line.get('box')
        if not _full_in_cell(b,region):continue
        if _center(b)[1]>_center(prior['box'])[1]+.025:continue
        # One local grid cell is only a hypothesis; avoid pulling a title
        # from the opposite side of a wide, unsplit flyer row.
        if abs(_center(b)[0]-_center(prior['box'])[0])>.32:continue
        value=str(line.get('text','')).strip()
        if not value or re.search(r'(?i)\b(?:F[ØO]R|TIDLIGERE)\b',value):continue
        eligible.append((i,line,value))
        tokens=set(norm_tokens(value))
        if (required and required <= tokens) or _compact_ocr_title(value)==target:
            hits.append((i,b,[i],[b],'same_cell_title_ocr_review_only'))
    # Exact two-line recovery, with hard x/y proximity and no extra words.
    # Price strings, footer text, unrelated captions and distant ads fail.
    for i,first,a in eligible:
        for j,second,b in eligible:
            if j==i:continue
            ab,bb=first['box'],second['box']
            ax,ay=_center(ab);bx,by=_center(bb)
            if not (0 < ay-by<=.060 and abs(ax-bx)<=.16):continue
            if not (_compact_ocr_title(a+b)==target):continue
            hits.append((i,ab,[i,j],[ab,bb],'two_line_exact_title_ocr_review_only'))
    return hits


def _catalog_recurrence_candidates(page, prior, before, region, anchor, all_offers):
    """Second-pass review hypotheses, never price verification.

    All three observations must still be physically inside ONE original or
    separately recomputed local cell. Only a unique whole-catalog offer can
    be accepted, and it must match the actual PRINTED current amount.
    """
    if (before is None or region is None or anchor is None or
            not _full_in_cell(prior.get('box'),region) or
            not all(_full_in_cell(b,region) for b in anchor['boxes']) or
            not _current_in_cell(anchor,prior,region)):
        return []
    matches=[]
    for row in all_offers:
        now=money(row.get('catalog_price_nok'))
        if now is None or now!=anchor['amount'] or before<=now:continue
        title=str(row.get('title',''))
        for idx,box,indices,boxes,method in _recovery_title_hits(page,title,prior,region):
            matches.append({'offer_number':row['offer_number'],
                'title':title,'tjek_current_price_nok':f'{now:.2f}',
                'prior_price_ocr_nok':f'{before:.2f}',
                'title_ocr_line':idx,'title_ocr_box':box,
                'title_ocr_lines':indices,'title_ocr_boxes':boxes,
                'current_ocr_lines':anchor['line_indices'],
                'current_ocr_boxes':anchor['boxes'],
                'current_ocr_parse':anchor['source'],
                'match_method':method,
                'source_tjek_page':row.get('page'),
                'distance_score':_candidate_score(prior,{'box':box},anchor)})
    if len({m['offer_number'] for m in matches})!=1:return []
    return sorted(matches,key=lambda m:m['distance_score'])[:1]


def _same_downward_cell(prior, box, region, *, tolerance=.024):
    if not _region_contains(box,region,margin=tolerance):return False
    # In observed flyers FØR badges sit above the body of the product advert.
    # With the saved schema's bottom-left y, lower-y means lower on page.
    # If a retailer instead puts the product title above FØR, abstain.
    return _center(box)[1] <= _center(prior['box'])[1]+.024


def _current_in_cell(current, prior, region):
    return any(_same_downward_cell(prior,b,region,tolerance=.014) for b in current['boxes'])


def _candidate_score(prior, title_hit, price_hit):
    px,py=_center(prior['box'])
    tx,ty=_center(title_hit['box'])
    bx,by=_center(price_hit['boxes'][0])
    # Repetition / adjacency cannot outweigh a bad exact price.
    return round(abs(px-bx)+abs(py-by)+.5*abs(px-tx)+.4*abs(py-ty),5)


def _region_gutter(image_path,region):
    """OpenCV pixel support for potential boundaries. Conservative, optional.

    Test edge occupancy near each grid boundary in the relevant vertical span.
    Detecting sparse edges is a hint only; similar background on both sides
    does not prove that an advert boundary exists.
    """
    if not image_path or not Path(image_path).is_file():return {'status':'image_missing'}
    im=cv2.imread(str(image_path))
    if im is None:return {'status':'image_unreadable'}
    h,w=im.shape[:2]
    gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
    gray=cv2.GaussianBlur(gray,(5,5),0)
    edges=cv2.Canny(gray,65,125)
    gutter=[]
    for axis in ('x0','x1'):
        pos=region[axis]
        if pos<.02 or pos>.98: continue
        col=int(pos*w)
        y0=int((1-region['y1'])*h)
        y1=int((1-region['y0'])*h)
        if y1-y0<20:continue
        stripe=edges[y0:y1,max(0,col-5):min(w,col+6)]
        if stripe.size==0:continue
        density=float((stripe>0).mean())
        if density<.055:
            gutter.append({'axis':axis,'x':round(pos,5),'edge_density':round(density,5)})
    return {'status':'possible_cv_gutter_review_only' if gutter else 'no_clear_cv_gutter',
            'gutter_hints':gutter,'image_examined':True}


def _price_anchor_rows(page, priors, tjek_offers, image_path=None):
    """Construct local row bands from PRINTED current prices and FØR marks.

    Bands are separate 2D strips; each band's current-price anchors establish
    independent horizontal ad cells.  No whole-page vertical column is inferred.
    All anchors come from OCR, and only amounts already observed in Tjek may act
    as current-price anchors.  The chosen cell remains a hypothesis, not a
    verified segmentation mask.
    """
    allowed={money(p.get('catalog_price_nok')) for p in tjek_offers}
    allowed.discard(None)
    currents=current_price_observations(page,allowed)
    # Collapse duplicate detection modes for one physical printed price.
    unique=[]
    for c in sorted(currents,key=lambda c:(_center(c['boxes'][0])[1],_center(c['boxes'][0])[0])):
        cx,cy=_center(c['boxes'][0])
        if any(c['amount']==u['amount'] and abs(cx-_center(u['boxes'][0])[0])<.021
               and abs(cy-_center(u['boxes'][0])[1])<.025 for u in unique):
            continue
        unique.append(c)
    currents=unique
    # Some physical adverts have OCR prices, but no Tjek placement ON THIS
    # page (a product can recur on a different page). Their printed price is
    # useful for GEOMETRY, never as evidence to link a FØR price to Tjek.
    layout_only=[]
    for candidate in current_price_observations(page, None):
        if candidate['amount'] in allowed:
            continue
        bx,by=_center(candidate['boxes'][0])
        if any(abs(bx-_center(q['boxes'][0])[0])<.025 and
               abs(by-_center(q['boxes'][0])[1])<.032 for q in currents):
            continue
        # An ordinary line containing a standalone number may be a size or
        # unrelated page decoration; only consider a tall printed price badge.
        if float(candidate['boxes'][0]['height']) < .029:
            continue
        layout_only.append(candidate)
    anchors=[]
    for p in priors:
        if _valid_box(p.get('box')):
            anchors.append({'kind':'prior','y':_center(p['box'])[1],
                            'top':float(p['box']['y'])+float(p['box']['height']),
                            'x':_center(p['box'])[0],'ref':p})
    for kind, observations in (('current',currents),('layout',layout_only)):
        for c in observations:
            b=c['boxes'][0]
            anchors.append({'kind':kind,'y':_center(b)[1],
                            'top':float(b['y'])+float(b['height']),
                            'x':_center(b)[0],'ref':c})
    if not anchors:return {},currents
    # Adjacent OCR badges are part of the same row if y close enough; limit
    # total spread to avoid a chain of unrelated rows collapsing together.
    ordered=sorted(anchors,key=lambda a:-a['y'])
    groups=[]
    for a in ordered:
        if groups and groups[-1][-1]['y']-a['y']<=.10 and groups[-1][0]['y']-a['y']<=.15:
            groups[-1].append(a)
        else:
            groups.append([a])
    # Top-to-bottom regions; put horizontal boundary just ABOVE the old-price
    # labels in the lower band.  This protects lower captions of upper adverts.
    bounds=[]
    for upper,lower in zip(groups,groups[1:]):
        lower_top=max(a['top'] for a in lower)
        upper_bottom=min(a['y'] for a in upper)
        guess=min(upper_bottom-.012,lower_top+.012)
        if guess<=lower_top:
            guess=(lower_top+upper_bottom)/2
        boundary,proof=_cv_horizontal_gutter(image_path,guess,lower_top,upper_bottom)
        bounds.append((max(0,min(1,boundary)),proof))
    cell_by_prior={}
    for gi,g in enumerate(groups):
        bottom=0 if gi==len(groups)-1 else bounds[gi][0]
        top=(min(1,max(a['top'] for a in g)+.065) if gi==0
             else bounds[gi-1][0])
        if gi==len(groups)-1:
            # Do not draw the last row through a footer/logo when the OCR
            # captions tell us where the product adverts actually finish.
            caption_bottoms=[]
            for a in g:
                if a['kind']!='current':continue
                amount=a['ref']['amount']
                for offer in tjek_offers:
                    if money(offer.get('catalog_price_nok'))!=amount:continue
                    for title_line in page.get('lines',[]):
                        b=title_line.get('box')
                        if not _valid_box(b):continue
                        if not _product_match(title_line.get('text',''),offer.get('title','')):continue
                        x,y=_center(b)
                        if abs(x-a['x'])>.37 or y>a['y']+.03 or a['y']-y>.30:continue
                        caption_bottoms.append(float(b['y']))
            if caption_bottoms:
                bottom=max(0,min(caption_bottoms)-.018)
            else:
                bottom=max(0,min(a['y'] for a in g)-.17)
        # Conflicting approximate bands should abstain rather than invent area.
        if top<=bottom:continue
        row_currents=[a['ref'] for a in g if a['kind']=='current']
        # A missing OCR price must not allow its FØR badge to be claimed by
        # a distant price from the neighboring advert. Reserve a price-less
        # cell for the FØR badge in that case (explicit abstention).
        slots=[{'x':a['x'],'current':a['ref'] if a['kind']=='current' else None, 'layout':a['ref'] if a['kind']=='layout' else None}
               for a in g if a['kind'] in ('current','layout')]
        for a in g:
            if a['kind']!='prior':continue
            if not slots or min(abs(a['x']-slot['x']) for slot in slots)>.19:
                slots.append({'x':a['x'],'current':None,'layout':None})
        slots.sort(key=lambda z:z['x'])
        centers=[slot['x'] for slot in slots]
        splits=[];split_evidence=[]
        for left,right in zip(centers,centers[1:]):
            midpoint=(left+right)/2
            split,ev=_cv_vertical_gutter(image_path,midpoint,left,right,bottom,top)
            splits.append(split);split_evidence.append(ev)
        for p in g:
            if p['kind']!='prior':continue
            x,y=p['x'],p['y']
            # A FØR mark must belong to exactly one current-price cell.
            for ci,slot in enumerate(slots):
                x0=0 if ci==0 else splits[ci-1]
                x1=1 if ci==len(slots)-1 else splits[ci]
                region={'x0':round(x0,6),'x1':round(x1,6),
                        'y0':round(bottom,6),'y1':round(top,6),
                        'status':'local_price_anchored_cell_review_only',
                        'row_from_top':gi,'column_within_row':ci,
                        'rows_on_page':len(groups),'columns_in_row':len(slots),
                        'horizontal_gutter':bounds[gi][1] if gi<len(bounds) else {'status':'page_edge'},
                        'vertical_gutter':(split_evidence[ci] if ci<len(splits)
                                           else split_evidence[ci-1] if ci else {'status':'no_neighbor'}),
                        'geometry_only_ocr_prices_on_page':len(layout_only)}
                if x0<=x<=x1 and bottom<=y<=top:
                    cell_by_prior[p['ref']['signal_id']]={'region':region,'current_anchor':slot['current'],
                                                          'row_currents':row_currents}
                    break
    # The initial group boundaries are shared across the width of the page.
    # Refine them independently for each x-lane using local vertical neighbors.
    # This prevents a Mortadella/Tiramisu boundary from being reused beneath
    # the Kjøttboller advert in another lane.
    # Preserve the last proven coarse layout as a separate, review-only
    # fallback. A pixel-only local seam must not delete OCR evidence that
    # already formed a unique prior + current + product triangle.
    for cell in cell_by_prior.values():
        cell['coarse_region'] = copy.deepcopy(cell['region'])
    _refine_lane_local_bands(cell_by_prior, currents+layout_only, image_path)
    return cell_by_prior,currents


def _lane_blank_horizontal(image_path, center_y, low_y, high_y, x_center, x_span):
    """Find a real background-colored gap locally around ONE current badge.

    The pixel test is used only to draw better REVIEW regions, never to
    certify a price match. Fall back to midpoint if the image is unavailable.
    """
    if not image_path or not Path(image_path).is_file():
        return center_y, {'status':'ocr_lane_midpoint_review_only'}
    features=_image_features(str(image_path))
    if features is None:
        return center_y, {'status':'ocr_lane_midpoint_review_only'}
    im,edges,bg=features
    h,w=im.shape[:2]
    # Measure the candidate seam across (nearly) the full local ad lane,
    # not only underneath the price badge. A tiny blank patch by the price
    # may cut through an actual product package elsewhere in the lane.
    xmin=max(0,x_center-max(.11,x_span*.46))
    xmax=min(1,x_center+max(.11,x_span*.46))
    x0=int(xmin*w);x1=int(xmax*w)
    lo=max(low_y+.012,center_y-.065)
    hi=min(high_y-.012,center_y+.065)
    if hi<=lo or x1-x0<35:
        return center_y, {'status':'ocr_lane_midpoint_review_only'}
    candidates=[]
    for y in np.arange(lo,hi,.0025):
        py=int((1-y)*h)
        stripe=im[max(0,py-2):min(h,py+3),x0:x1]
        edge=edges[max(0,py-2):min(h,py+3),x0:x1]
        if not stripe.size:continue
        background=float((np.max(np.abs(stripe.astype(np.int16)-bg),axis=2)<24).mean())
        density=float((edge>0).mean())
        if background>.965 and density<.015:
            candidates.append((background,density,abs(y-center_y),float(y)))
    if not candidates:
        return center_y, {'status':'ocr_lane_midpoint_review_only'}
    # Favor actual open space, then closeness to OCR's midpoint.
    bgval,ed,_,result=max(candidates,key=lambda a:(a[0],-a[1],-a[2]))
    return result, {'status':'cv_local_lane_gap_review_only',
                    'y':round(result,6),'background_fraction':round(bgval,3),
                    'edge_fraction':round(ed,4)}


def _refine_lane_local_bands(cells, current_prices, image_path):
    """Allow column-specific horizontal boundaries; no whole-page y cuts.

    A neighbor must have roughly the SAME x as the printed price. Prices
    across unrelated columns must never influence a cell's vertical extent.
    """
    anchors=[]
    for c in current_prices:
        b=c['boxes'][0]
        if _valid_box(b):
            x,y=_center(b)
            if not any(abs(x-t[0])<.02 and abs(y-t[1])<.027 for t in anchors):
                anchors.append((x,y,c))
    for record in cells.values():
        own=record.get('current_anchor')
        if own is None:continue
        b=own['boxes'][0]
        cx,cy=_center(b)
        region=record['region']
        near=[(x,y) for x,y,c in anchors if c is not own and
              abs(x-cx)<min(.22,max(.12,(region['x1']-region['x0'])*.75)) and
              abs(y-cy)>.11]
        higher=sorted((y for _,y in near if y>cy))
        lower=sorted((y for _,y in near if y<cy),reverse=True)
        # Only adjust an edge if there is actual current-price geometry to
        # support it; do not expand into another neighbor's physical advert.
        for direction,neighbors in (('top',higher),('bottom',lower)):
            if not neighbors:continue
            ny=neighbors[0]
            midpoint=(cy+ny)/2
            if direction=='top':
                if midpoint>=region['y1']+.075:continue
                boundary,evidence=_lane_blank_horizontal(image_path,midpoint,cy,ny,cx,
                    region['x1']-region['x0'])
                if evidence['status']!='cv_local_lane_gap_review_only':
                    continue
                if (max(float(b['y'])+float(b['height'])+.008,
                        region['y0']+.02) < boundary <=1):
                    # For a local row below a neighboring price, its own TOP
                    # must not extend into the ad containing that neighbor.
                    if boundary < region['y1'] or boundary <=region['y1']+.07:
                        region['y1']=round(boundary,6)
                        region['local_top_boundary']=evidence
            else:
                if midpoint<=region['y0']-.075:continue
                boundary,evidence=_lane_blank_horizontal(image_path,midpoint,ny,cy,cx,
                    region['x1']-region['x0'])
                if evidence['status']!='cv_local_lane_gap_review_only':
                    continue
                if (0<=boundary < float(b['y'])-.008 and boundary < region['y1']-.02):
                    if boundary>region['y0'] or boundary>=region['y0']-.07:
                        region['y0']=round(boundary,6)
                        region['local_bottom_boundary']=evidence


def _cv_horizontal_gutter(image_path,guess,lower_top,upper_bottom):
    if not image_path or not Path(image_path).is_file():
        return guess,{'status':'ocr_geometry_only'}
    features=_image_features(str(image_path))
    if features is None:return guess,{'status':'background_not_uniform'}
    im,edges,bg=features
    h,w=im.shape[:2]
    # Never place the divider on a FØR badge or on the neighboring current
    # price.  Search LOCAL inter-row area, not all of the page.
    lo=max(lower_top+.009,guess-.045)
    hi=min(upper_bottom-.009,guess+.045)
    if hi<=lo:return guess,{'status':'no_vertical_gap'}
    candidates=[]
    for y in np.arange(lo,hi,.0025):
        py=int((1-y)*h)
        stripe=im[max(0,py-2):min(h,py+3),int(.03*w):int(.97*w)]
        edge=edges[max(0,py-2):min(h,py+3),int(.03*w):int(.97*w)]
        if not stripe.size:continue
        frac=float((np.max(np.abs(stripe.astype(np.int16)-bg),axis=2)<25).mean())
        ed=float((edge>0).mean())
        if frac>.965 and ed<.012:
            candidates.append((abs(y-guess),float(y),frac,ed))
    if not candidates:return guess,{'status':'no_clear_horizontal_gutter'}
    _,y,frac,ed=min(candidates)
    return y,{'status':'cv_horizontal_gutter_hint_review_only',
               'y':round(y,6),'background_fraction':round(frac,3),
               'edge_fraction':round(ed,4)}


def _cv_vertical_gutter(image_path,guess,left,right,y0,y1):
    if not image_path or not Path(image_path).is_file():
        return guess,{'status':'ocr_geometry_only'}
    features=_image_features(str(image_path))
    if features is None:return guess,{'status':'background_not_uniform'}
    im,edges,bg=features
    h,w=im.shape[:2]
    # Scan only THIS ROW: the same x line must not split unrelated adverts in
    # the row above or below.  Truncate away from border text/badges.
    start=int((1-y1)*h);end=int((1-y0)*h)
    if end-start<90:return guess,{'status':'row_too_short_for_gutter'}
    lo=max(left+.032,guess-.16)
    hi=min(right-.032,guess+.16)
    candidates=[]
    for x in np.arange(lo,hi,.003):
        px=int(x*w)
        stripe=im[start:end,max(0,px-3):min(w,px+4)]
        edge=edges[start:end,max(0,px-3):min(w,px+4)]
        if not stripe.size:continue
        frac=float((np.max(np.abs(stripe.astype(np.int16)-bg),axis=2)<23).mean())
        ed=float((edge>0).mean())
        if frac>.89 and ed<.025:
            candidates.append((float(x),frac,ed))
    if not candidates:return guess,{'status':'no_clear_vertical_gutter'}
    # Use the middle of the strongest same-background blank run, avoiding a
    # decorative empty patch at the far edge of a price tag.
    best=min(candidates,key=lambda v:(-v[1],v[2],abs(v[0]-guess)))
    return best[0],{'status':'cv_vertical_gutter_hint_review_only',
                     'x':round(best[0],6),'background_fraction':round(best[1],3),
                     'edge_fraction':round(best[2],4)}


def _full_in_cell(b,region,*,min_overlap=.75):
    return (_region_contains(b,region) and
            _overlap_ratio(b,region)>=min_overlap)


def _image_border_warnings(image_path, region):
    """Warn when an inferred rectangular boundary traverses visible pixels.

    This is intentionally NOT an object detector nor physical ad proof.
    The foreground is measured against the page's modal flat background,
    and text/image overlap may trigger a conservative warning.
    """
    if not image_path or not Path(image_path).is_file():
        return {'status':'no_image_for_border_check','edges':[]}
    features=_image_features(str(image_path))
    if features is None:
        return {'status':'background_unreliable','edges':[]}
    im,_,background=features; h,w=im.shape[:2]
    result=[]
    for axis in ('x0','x1','y0','y1'):
        value=float(region[axis])
        if not .015 < value < .985: continue
        if axis.startswith('x'):
            pixel=int(value*w)
            top=int((1-region['y1'])*h);bottom=int((1-region['y0'])*h)
            pad=max(2,int((bottom-top)*.06))
            stripe=im[max(0,top+pad):min(h,bottom-pad),max(0,pixel-2):min(w,pixel+3)]
        else:
            pixel=int((1-value)*h)
            left=int(region['x0']*w);right=int(region['x1']*w)
            pad=max(2,int((right-left)*.06))
            stripe=im[max(0,pixel-2):min(h,pixel+3),max(0,left+pad):min(w,right-pad)]
        if stripe.size<200:continue
        fraction=float((np.max(np.abs(stripe.astype(np.int16)-background),axis=2)<27).mean())
        result.append({'edge':axis,'background_fraction':round(fraction,3),
                       'warning':'possible_content_intersection' if fraction<.86 else 'no_large_foreground_detected'})
    return {'status':('possible_content_intersection' if any(x['warning']=='possible_content_intersection' for x in result)
                      else 'no_large_foreground_detected'), 'edges':result}


@lru_cache(maxsize=8)
def _blue_price_badge_boxes(image_path):
    """Possible solid blue, approximately square printed price plaques.

    Pixel geometry is an OPTIONAL review aid, not price OCR or an assurance
    that an advert has been segmented. JPEG backgrounds/product art without a
    clear, separate price plaque are deliberately not matched by this route.
    Coordinates use bottom-origin normalized NutriValue page geometry.
    """
    if not image_path or not Path(image_path).is_file():return ()
    im=cv2.imread(str(image_path),cv2.IMREAD_COLOR)
    if im is None:return ()
    h,w=im.shape[:2]
    if h<400 or w<400:return ()
    hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
    # Bold saturated blue; exclude teal, low-saturation photographs and text.
    mask=cv2.inRange(hsv,(102,135,55),(119,255,225))
    count,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
    result=[]
    for x,y,bw,bh,area in stats[1:]:
        if bw<.095*w or bh<.055*h or area<.006*w*h:continue
        if not .83 <= bw/bh <= 1.8:continue
        if area/(bw*bh)<.57:continue
        # Reject large full-width banners/page backgrounds and tiny logos.
        if bw>.55*w or bh>.34*h:continue
        region={'x0':float(x/w),'x1':float((x+bw)/w),
                'y0':float(1-(y+bh)/h),'y1':float(1-y/h)}
        result.append(region)
    return tuple(sorted(result,key=lambda b:(-b['y1'],b['x0'])))


def _badge_for_prior(prior, badges, priors_on_page):
    """One FØR marker overlapping the TOP of one badge, no shared badges."""
    if not _valid_box(prior.get('box')):return None
    pb=prior['box']; px,py=_center(pb)
    candidates=[]
    for badge in badges:
        horizontal=min(pb['x']+pb['width'],badge['x1'])-max(pb['x'],badge['x0'])
        if horizontal < min(.022,pb['width']*.20):continue
        if not -.032<=py-badge['y1']<=.065:continue
        candidates.append(badge)
    if len(candidates)!=1:return None
    badge=candidates[0]
    for other in priors_on_page:
        if other is prior or not _valid_box(other.get('box')):continue
        other_pb=other['box'];ox,oy=_center(other_pb)
        horizontal=min(other_pb['x']+other_pb['width'],badge['x1'])-max(other_pb['x'],badge['x0'])
        if horizontal >= min(.022,other_pb['width']*.20) and -.032 <= oy-badge['y1'] <= .065:
            return None
    return badge


def _badge_printed_price_hits(page, badge, allowed_prices):
    """Read OCR price evidence ON a detected plaque, not Tjek-imputed money.

    Only a compact numeric suffix fused with packaging text is new here; a
    partial OCR line must overlap the actual plaque, have large enough glyph
    geometry, and equal one observed Tjek amount. There is no standalone
    inference from the badge pixels or the catalog price.
    """
    observed=current_price_observations(page, allowed_prices)
    output=[]
    for price in observed:
        if all(_overlap_ratio(b,badge)>=.48 for b in price['boxes']):
            output.append(price)
    for i,line in enumerate(page.get('lines',[])):
        b=line.get('box')
        if not _valid_box(b) or _overlap_ratio(b,badge)<.32:continue
        label=str(line.get('text','')).strip()
        if re.match(r'(?i)^\s*(?:F[ØO]R|TIDLIGERE)(?:\b|\s)',label):continue
        # A high-visibility price glyph can be merged into adjacent packaging
        # brand text by OCR (e.g. "HATT 3990"). Do NOT reinterpret arbitrary
        # 4-digit identifiers, catalog descriptions, masses or dates.
        m=re.fullmatch(r'[A-Za-zÆØÅæøå]{3,15}\s+(\d{3,4})',label)
        if not m or float(b['height'])<.023:continue
        amount=Decimal(int(m.group(1)))/100
        if amount not in allowed_prices:continue
        output.append({'amount':amount,'line_indices':[i],'boxes':[b],
                       'source':'blue_plaque_embedded_compact_ocr_review_only'})
    return list({(tuple(x['line_indices']),x['amount']):x for x in output}.values())


def _caption_below_badge(page, title, badge, all_badges):
    """Find entire OCR title below plaque, confined by neighboring plaques.

    Flyer captions can be 0.5 page-heights below badges due to large product
    photography. Horizontal nearest-plaque assignment and the next plaque
    below are hard constraints; the pixel-derived area is NOT a verified ad.
    """
    required=set(norm_tokens(title));target=_compact_ocr_title(title)
    if len(target)<8 or not required:return []
    cx=(badge['x0']+badge['x1'])/2
    same_row=[b for b in all_badges if b is not badge and
              abs((b['y0']+b['y1'])/2-(badge['y0']+badge['y1'])/2)<.115]
    # For every title, assign to the nearest horizontal plaque in its row.
    left_limit=0.;right_limit=1.
    for other in same_row:
        ox=(other['x0']+other['x1'])/2
        boundary=(cx+ox)/2
        if ox<cx:left_limit=max(left_limit,boundary)
        else:right_limit=min(right_limit,boundary)
    # A lower price plaque in this x-lane starts a different advert.
    lower_tops=[b['y1'] for b in all_badges if b is not badge and
                b['y1']<badge['y0']-.045 and
                min(b['x1'],badge['x1'])-max(b['x0'],badge['x0'])>.08]
    floor=max(lower_tops)+.008 if lower_tops else max(.025,badge['y0']-.60)
    hits=[]
    for i,ln in enumerate(page.get('lines',[])):
        b=ln.get('box');value=str(ln.get('text','')).strip()
        if not _valid_box(b) or not value:continue
        x,y=_center(b)
        if not (left_limit <= x <= right_limit):continue
        if not (floor<=y<=badge['y0']+.045):continue
        if abs(x-cx)>.28:continue
        if re.search(r'(?i)\b(?:F[ØO]R|TIDLIGERE)\b',value):continue
        # Full exact title, allowing descriptive words AFTER it. For package
        # OCR the same exact required title tokens may appear with brand text
        # prepended. Short shared one-word labels are not enough.
        tokens=set(norm_tokens(value))
        compact=_compact_ocr_title(value)
        if required <= tokens or compact==target:
            hits.append((i,b))
    return hits


def _plaque_layout_recovery(page,prior,before,priors_on_page,all_offers,image_path):
    """Third pass for an *unresolved* FØR, with real pixels + source OCR.

    Produces a candidate only when old badge, printed OCR current amount, and
    entire OCR product title uniquely occupy one plausible vertical advert.
    This never verifies a past price, sale condition, package variant or SKU.
    """
    if before is None or not image_path or not _valid_box(prior.get('box')):
        return []
    badges=_blue_price_badge_boxes(str(image_path))
    badge=_badge_for_prior(prior,badges,priors_on_page)
    if badge is None:return []
    relevant_prices={money(r.get('catalog_price_nok')) for r in all_offers}
    relevant_prices.discard(None)
    prices=_badge_printed_price_hits(page,badge,relevant_prices)
    matches=[]
    for price in prices:
        if price['amount'] >= before:continue
        for offer in all_offers:
            now=money(offer.get('catalog_price_nok'))
            if now!=price['amount']:continue
            title=str(offer.get('title',''))
            for i,b in _caption_below_badge(page,title,badge,badges):
                matches.append({'offer_number':offer['offer_number'],
                    'title':title,'tjek_current_price_nok':f'{now:.2f}',
                    'prior_price_ocr_nok':f'{before:.2f}',
                    'title_ocr_line':i,'title_ocr_box':b,
                    'title_ocr_lines':[i],'title_ocr_boxes':[b],
                    'current_ocr_lines':price['line_indices'],
                    'current_ocr_boxes':price['boxes'],
                    'current_ocr_parse':price['source'],
                    'match_method':'blue_plaque_vertical_layout_review_only',
                    'source_tjek_page':offer.get('page'),
                    'blue_plaque_bbox':badge,
                    'distance_score':_candidate_score(prior,{'box':b},price)})
    if len({m['offer_number'] for m in matches})!=1:return []
    if len({tuple(m['current_ocr_lines']) for m in matches})!=1:return []
    return sorted(matches,key=lambda m:m['distance_score'])[:1]


def link_prior_prices_by_regions(ocr, semantic_ledger, tjek_offers, *, image_dir=None):
    """Review-only local ad cells using printed prices; never writes prices.

    FØR, OCR current-price badge and OCR product name must each be IN the
    exact same local 2D cell. A reused printed price/title or contradictory
    previous price is rejected. Image seams are supporting hints, never proof.
    """
    pages={p['page']:p for p in ocr['pages']}
    by_page=defaultdict(list)
    for row in tjek_offers:by_page[row['page']].append(row)
    # Full catalog is examined ONLY when the original same-page procedure
    # has no candidate. This does not change the original successful cells.
    prior_by_page=defaultdict(list)
    for s in semantic_ledger:
        if s['kind']=='prior_price':prior_by_page[s['page']].append(s)
    out=[]
    for page_no,priors in sorted(prior_by_page.items()):
        catalog_cells=None  # Reuse per-page second pass, never re-run CV per prior.
        page=pages[page_no]
        image_path=Path(image_dir)/f'page-{page_no:02d}.jpg' if image_dir else None
        cells,currents=_price_anchor_rows(page,priors,by_page[page_no],image_path)
        for prior in priors:
            cell=cells.get(prior['signal_id'])
            region=cell['region'] if cell else None
            before=money(_prior_money(prior))
            rejected=[];suggestions=[]
            source_text=prior.get('context','')
            # Prior ranges e.g. FØR 148,40-150,40 are not one old price.
            range_price=bool(re.search(r'\d+[.,]\d{2}\s*[-–]\s*\d+[.,]\d{2}',source_text))
            def candidates_within(candidate_region):
                matches=[]; rejected_local=[]
                if not cell or cell['current_anchor'] is None or before is None or range_price:
                    return matches, rejected_local
                anchor=cell['current_anchor']
                if not _full_in_cell(prior.get('box'),candidate_region):
                    return matches, rejected_local
                if not all(_full_in_cell(b,candidate_region) for b in anchor['boxes']):
                    return matches, rejected_local
                for row in by_page[page_no]:
                    now=money(row.get('catalog_price_nok'))
                    if now is None or now!=anchor['amount']:continue
                    for idx,line in enumerate(page.get('lines',[])):
                        b=line.get('box')
                        if not _full_in_cell(b,candidate_region):continue
                        if not _product_match(line.get('text',''),row.get('title','')):continue
                        if _center(b)[1]>_center(prior['box'])[1]+.025:continue
                        if before<=now:
                            rejected_local.append({'offer_number':row['offer_number'],
                              'title':row['title'],'current_price_nok':f'{now:.2f}',
                              'reason':'prior_not_above_current'})
                            continue
                        matches.append({'offer_number':row['offer_number'],
                          'title':row['title'],'tjek_current_price_nok':f'{now:.2f}',
                          'prior_price_ocr_nok':f'{before:.2f}',
                          'title_ocr_line':idx,'title_ocr_box':b,
                          'current_ocr_lines':anchor['line_indices'],
                          'current_ocr_boxes':anchor['boxes'],
                          'current_ocr_parse':anchor['source'],
                          'distance_score':_candidate_score(prior,{'box':b},anchor)})
                return matches,rejected_local

            if region is not None:
                suggestions,rejected=candidates_within(region)
                coarse_region=cell.get('coarse_region') if cell else None
                # Only restore a removed match if the COARSE (previously
                # deployed) cell proves one unique Tjek item by all three
                # separate OCR anchors. This is not a general expanded radius.
                # The original refined region remains available as evidence.
                if not suggestions and coarse_region and coarse_region!=region:
                    coarse_candidates,coarse_rejections=candidates_within(coarse_region)
                    unique_ids={c['offer_number'] for c in coarse_candidates}
                    if len(unique_ids)==1 and coarse_candidates:
                        refined_region=region
                        region=copy.deepcopy(coarse_region)
                        region['status']='coarse_proven_triangle_fallback_review_only'
                        region['refinement_rejected']='would_remove_unique_ocr_price_title_prior_triangle'
                        region['refined_region_for_diagnostics']=refined_region
                        suggestions=coarse_candidates
                        rejected=coarse_rejections
            # Recovery pass: a flyer may repeat an offer on a page that Tjek
            # assigns to its original placement. The first pass above always
            # remains authoritative for already-resolved observations.
            if not suggestions and not rejected and not range_price and before is not None:
                recovery_cell=cell
                if recovery_cell is None or recovery_cell.get('current_anchor') is None:
                    # A page-local price whitelist excluded repeated printed
                    # offers. Rebuild separately using the catalog-wide price
                    # vocabulary, WITHOUT modifying the original cell layout.
                    if catalog_cells is None:
                        catalog_cells,_=_price_anchor_rows(page,priors,tjek_offers,image_path)
                    recovery_cell=catalog_cells.get(prior['signal_id'])
                if recovery_cell and recovery_cell.get('current_anchor'):
                    recovery_region=recovery_cell['region']
                    recovered=_catalog_recurrence_candidates(
                        page,prior,before,recovery_region,
                        recovery_cell['current_anchor'],tjek_offers)
                    if recovered:
                        # An alternative cell must not silently overwrite the
                        # first-pass geometry. Preserve it for audit.
                        if recovery_cell is not cell:
                            original_region=region
                            region=copy.deepcopy(recovery_region)
                            region['first_pass_region_for_diagnostics']=original_region
                        else:
                            region=copy.deepcopy(region)
                        region['status']='catalog_ocr_triangle_recovery_review_only'
                        suggestions=recovered
            # Only unresolved observations may use optional pixel-supported
            # vertical ad geometry. The original and recurrence passes stay
            # intact; never broaden their accepted candidate neighborhoods.
            if (not suggestions and not rejected and not range_price and
                    before is not None and image_path and image_path.is_file()):
                plaque_candidates=_plaque_layout_recovery(
                    page,prior,before,priors,tjek_offers,image_path)
                if plaque_candidates:
                    original_region=copy.deepcopy(region)
                    # Keep the original cell geometry in the primary `region`
                    # field: downstream QA and overlays must not mistake the
                    # pixel plaque itself for a verified full-ad boundary.
                    if region is None:
                        region=copy.deepcopy(plaque_candidates[0]['blue_plaque_bbox'])
                    else:
                        region=copy.deepcopy(region)
                    region['status']='blue_plaque_vertical_ad_hypothesis_review_only'
                    region['blue_price_plaque_bbox']=copy.deepcopy(
                        plaque_candidates[0]['blue_plaque_bbox'])
                    region['first_pass_region_for_diagnostics']=original_region
                    suggestions=plaque_candidates
            reason=None
            if not suggestions and not range_price and cell is not None:
                if cell['current_anchor'] is None:
                    reason='no_current_price_badge_in_local_cell'
                elif before is None:
                    reason='old_price_unparseable'
                elif not _full_in_cell(prior.get('box'),region):
                    reason='old_price_box_crosses_local_boundary'
                elif rejected:
                    reason='old_price_is_not_higher_than_current_price'
                else:
                    matched_price_offers=[r for r in by_page[page_no]
                        if money(r.get('catalog_price_nok'))==cell['current_anchor']['amount']]
                    title_hits=[]
                    for r in matched_price_offers:
                        hits=[]
                        for idx,ln in enumerate(page.get('lines',[])):
                            if _valid_box(ln.get('box')) and _product_match(ln.get('text',''),r.get('title','')):
                                hits.append({'line_index':idx,
                                             'within_cell':_full_in_cell(ln['box'],region),
                                             'text':str(ln.get('text',''))[:90]})
                        title_hits.append({'offer_number':r['offer_number'],
                                           'title':r.get('title',''),
                                           'title_hits':hits})
                    reason='current_price_found_but_product_title_not_proven_in_cell'
                    diagnostic={'reason':reason,
                                'anchor_source':cell['current_anchor'].get('source'),
                                'anchor_price_nok':str(cell['current_anchor']['amount']),
                                'price_matching_tjek_offers':title_hits}
            if reason and reason!='current_price_found_but_product_title_not_proven_in_cell':
                diagnostic={'reason':reason}
            if not suggestions and cell is None and not range_price:
                diagnostic={'reason':'no_compatible_local_price_cell'}
            if not suggestions and not range_price:
                # Help pinpoint which OCR text is missing or misread without
                # ever inventing a product title from the Tjek API. Local
                # context is evidence for a HUMAN to inspect, not an input
                # into automatic linking.
                context_lines=[]
                for idx,ln in enumerate(page.get('lines',[])):
                    b=ln.get('box')
                    if region and _valid_box(b) and _region_contains(b,region):
                        distance=abs(_center(b)[1]-_center(prior['box'])[1])
                        if idx != prior.get('line_index'):
                            context_lines.append((distance,idx,str(ln.get('text',''))[:120]))
                context_lines.sort()
                diagnostic['nearby_ocr_lines_in_cell']=[
                    {'line_index':idx,'text':text} for _,idx,text in context_lines[:12]]
            suggestions.sort(key=lambda c:c['distance_score'])
            unique={}
            for c in suggestions:unique.setdefault(c['offer_number'],c)
            candidates=list(unique.values())
            if range_price:status='unresolved_prior_price_range'
            elif not _valid_box(prior.get('box')):status='unresolved_invalid_geometry'
            elif not region:status='unresolved_no_local_price_cell'
            elif len(candidates)>1:status='ambiguous_multiple_products_in_region'
            elif candidates:status='region_candidate_review_only'
            elif rejected:status='rejected_prior_not_above_current'
            else:status='unresolved_missing_same_region_evidence'
            out.append({'signal_id':prior['signal_id'],'page':page_no,
                        'prior_ocr_line':prior['line_index'],
                        'ocr_prior_price_nok':f'{before:.2f}' if before is not None else None,
                        'status':status,'region':region,
                        'visual_boundary':(_region_gutter(image_path,region) if region
                                            else {'status':'no_local_price_cell'}),
                        'visual_border_warning':(_image_border_warnings(image_path,region)
                                                 if region else {'status':'no_local_price_cell','edges':[]}),
                        'candidate_products':candidates,'rejected_matches':rejected,
                        'source_box':prior.get('box'), 'source_text':source_text,
                        'diagnostic':diagnostic if not suggestions and not range_price else None,
                        'old_price_verified':False,'budget_price_modified':False})
    # Two FØR badges must not claim one OCR current-price or Tjek item.
    reused=defaultdict(list)
    for rec in out:
        if rec['status']!='region_candidate_review_only':continue
        chosen=rec['candidate_products'][0]
        reused[('item',rec['page'],chosen['offer_number'])].append(rec)
        for line_idx in chosen['current_ocr_lines']:
            reused[('price_line',rec['page'],line_idx)].append(rec)
    conflicting={r['signal_id'] for records in reused.values() if len(records)>1 for r in records}
    for rec in out:
        if rec['signal_id'] in conflicting:
            rec['status']='ambiguous_reused_item_or_price_anchor'
    return out


def render_region_overlay(image_path,results,out_path):
    """Diagnostic overlay; not a segmentation mask / proof of accuracy."""
    im=cv2.imread(str(image_path))
    if im is None: raise ValueError('Cannot open image for overlay')
    h,w=im.shape[:2]
    def xy(x,y):return (int(x*w),int((1-y)*h))
    colors={'region_candidate_review_only':(64,170,30),
            'ambiguous_multiple_products_in_region':(5,160,230),
            'rejected_prior_not_above_current':(20,40,220)}
    for r in results:
        b=r['region']
        if b is None:continue
        col=colors.get(r['status'],(150,80,140))
        # Do not present a border which may cut across packaging as a
        # confirmed green physical ad boundary. Dashed orange means review.
        if r.get('visual_border_warning',{}).get('status')=='possible_content_intersection':
            points=[xy(b['x0'],b['y1']),xy(b['x1'],b['y1']),
                    xy(b['x1'],b['y0']),xy(b['x0'],b['y0'])]
            warning_color=(0,170,255)
            for st,en in zip(points,points[1:]+points[:1]):
                dist=math.dist(st,en)
                if dist<1:continue
                for k in range(0,int(dist),22):
                    u=k/dist;v=min(k+11,dist)/dist
                    aa=(int(st[0]+(en[0]-st[0])*u),int(st[1]+(en[1]-st[1])*u))
                    bb=(int(st[0]+(en[0]-st[0])*v),int(st[1]+(en[1]-st[1])*v))
                    cv2.line(im,aa,bb,warning_color,3)
        else:
            cv2.rectangle(im,xy(b['x0'],b['y1']),xy(b['x1'],b['y0']),col,3)
        pb=r['source_box']
        cv2.rectangle(im,xy(pb['x'],pb['y']+pb['height']),xy(pb['x']+pb['width'],pb['y']), (0,0,255),3)
        for c in r['candidate_products']:
            cb=c['title_ocr_box']
            cv2.rectangle(im,xy(cb['x'],cb['y']+cb['height']),xy(cb['x']+cb['width'],cb['y']),(0,200,255),2)
            for box in c['current_ocr_boxes']:
                cv2.rectangle(im,xy(box['x'],box['y']+box['height']),xy(box['x']+box['width'],box['y']),(255,180,0),2)
    Path(out_path).parent.mkdir(parents=True,exist_ok=True)
    if not cv2.imwrite(str(out_path),im):raise OSError('Failed to save overlay')
