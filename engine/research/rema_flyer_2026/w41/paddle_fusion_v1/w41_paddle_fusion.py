#!/usr/bin/env python3
"""Review-only evidence fusion of W41 targeted PP-OCRv5 crops with V2 Vision geometry.

Input OCR text/boxes only; held-out labels are used AFTER inference for scoring.
Only a precisely-matched local crop pair may change a proposed current price.
Conflicts remain explicit and nothing is automatically published or verified.
"""
from __future__ import annotations
import argparse
from collections import Counter
import copy
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct

from w41_ab_review import checked_vision, private_path, asbox, center, score
from w41_ab_refine_v2 import product_caption_evidence, reference_only_review_rows, validate_ref

ISSUES = { (4,1):'FOR 103,00', (4,2):'FØR 64,90', (13,3):'FØR 84,90',
          (20,4):'FØR 51,50', (20,5):'FØR 99,80' }
PRIOR_RE = re.compile(r'F[ØO0]R\s*([1-9]\d{0,2})[,.]([0-9]{2})',re.I)
NPRICE_RE = re.compile(r'^[1-9]\d{0,4}$')

def amt(s):
    if not re.fullmatch(r'[1-9]\d{0,2}[.]\d{2}',s):return None
    return f'{Decimal(s):.2f}'

def png_size(path):
    with path.open('rb') as f: header=f.read(24)
    if len(header)!=24 or header[:8]!=b'\x89PNG\r\n\x1a\n' or header[12:16]!=b'IHDR':
        raise ValueError(f'Not PNG: {path.name}')
    return struct.unpack('>II',header[16:24])

def clip(b,mode):
    if mode=='price':return max(0,b['x']-.085),min(1,b['x']+b['width']+.115), max(0,b['y']-.14),min(1,b['y']+b['height']+.038)
    if mode=='advert':return max(0,b['x']-.31),min(1,b['x']+b['width']+.17),max(0,b['y']-.34),min(1,b['y']+b['height']+.055)
    raise ValueError('unknown crop')

def fullbox(local,anchor,mode,wh):
    x0,x1,y0,y1=clip(anchor,mode);w,h=wh
    lx0,ly0,lx1,ly1=local
    return dict(x=x0+lx0/w*(x1-x0), y=y0+(h-ly1)/h*(y1-y0),
                width=(lx1-lx0)/w*(x1-x0),height=(ly1-ly0)/h*(y1-y0))

def role_evidence(lines, expected_prior, mode):
    """Group large CURRENT price glyphs below FØR and above product label.

    No ground-truth/product matching used. The '6 PK' on Kvikk is excluded
    by x alignment with prior price band. Unit price has tiny glyphs below.
    """
    prior=[]
    for line in lines:
        m=PRIOR_RE.search(line['text'].replace(' ','').upper())
        if m:prior.append((f'{int(m[1])}.{m[2]}',line))
    matching=[(p,ln) for p,ln in prior if p==expected_prior]
    if len(matching)!=1:return {'prior':None,'current':None,'reason':'expected_prior_not_unique'}
    p, pline=matching[0]
    pb=pline['box'];prior_x=(pb[0]+pb[2])/2
    # The price crop may include a neighboring printed ad: use FØR as anchor.
    major_min=100 if mode=='price' else 42
    # A wide advert crop can contain *two rows* of offers. Do not attach
    # the lower price to an upper FØR anchor, even in the same column.
    next_priors=[ln['box'][1] for v,ln in prior if ln is not pline and ln['box'][1]>pb[3]
                 and abs((ln['box'][0]+ln['box'][2])/2-prior_x)<85]
    row_end=min([pb[3]+(300 if mode=='price' else 130)]+[y-2 for y in next_priors])
    candidates=[]
    for line in lines:
        txt=line['text'].strip();bx=line['box'];w=bx[2]-bx[0];h=bx[3]-bx[1]
        if bx[1]<pb[3]+3 or bx[1]>row_end:continue
        # Major figures are much larger than price-per-kg and captions.
        if bx[0]<prior_x-90 or bx[0]>prior_x+135:continue
        if NPRICE_RE.fullmatch(txt) and h>=major_min and w>=(65 if mode=='price' else 29) and bx[0]>prior_x-90:
            if 4<=len(txt)<=5 and h>= (150 if mode=='price' else 70):
                v=amt(txt[:-2]+'.'+txt[-2:])
                if v:candidates.append((v,line,'compact'))
            elif len(txt)<=3:
                candidates.append((txt,line,'major'))
    full=[x for x in candidates if x[2]=='compact']
    if full:
        if len({r[0] for r in full})!=1:return {'prior':p,'current':None,'reason':'ambiguous_compact'}
        v=full[0][0]
        return {'prior':p,'current':v,'reason':'compact_current_glyph','box':full[0][1]['box'],'text':full[0][1]['text']}
    majors=[x for x in candidates if x[2]=='major']
    proposals=[]
    for major,ml,_ in majors:
        mb=ml['box'];h=mb[3]-mb[1]
        for cl in lines:
            ct=cl['text'].strip();cb=cl['box'];ch=cb[3]-cb[1]
            if not re.fullmatch(r'\d{2}',ct):continue
            # cents are smaller, to the right, and vertically overlap the major digits
            if not (.23*h < ch < .83*h and cb[0] >= mb[0]+.58*(mb[2]-mb[0])
                    and cb[0] < mb[2]+55 and cb[1]>=mb[1]-10 and cb[1]<mb[3]):continue
            v=amt(major+'.'+ct)
            if v:proposals.append((v,[min(mb[0],cb[0]),min(mb[1],cb[1]),max(mb[2],cb[2]),max(mb[3],cb[3])]))
    if len(set(x[0] for x in proposals))==1 and proposals:
        return {'prior':p,'current':proposals[0][0],'reason':'spatial_major_cents_pair','box':proposals[0][1]}
    return {'prior':p,'current':None,'reason':'no_unambiguous_current_glyph_pair'}

def validate_inputs(v2,vision,paddle_dir,crops_dir):
    if v2.get('schema')!='nutrivalue_w41_ab_evidence_v2' or v2.get('week')!='2026-W41' or len(v2.get('candidates',[]))!=39 or v2.get('visual_labels_used_in_inference') is not False or v2.get('tjek_catalog_linked') is not False:
        raise ValueError('Requires unlinked 39-candidate W41 V2 evidence')
    checked_vision(vision)
    if v2.get('v2_vision_sha256') is None:raise ValueError('V2 evidence lacks OCR provenance hash')
    manifest=json.loads((crops_dir/'crops-manifest.json').read_text())
    if manifest.get('week')!='2026-W41' or manifest.get('crop_count')!=10 or len(manifest.get('crops',[]))!=10:
        raise ValueError('Invalid W41 crop manifest')
    if {x['file'] for x in manifest['crops']} != {f'p{pg:02d}-issue-{i:02d}-{mode}.png' for pg,i in ISSUES for mode in ('price','advert')}:
        raise ValueError('Unexpected/missing crop entries')
    outputs={}
    for (pg,i),txt in ISSUES.items():
        matching=[r for r in v2['candidates'] if r['page']==pg and r['prior']['text']==txt]
        if len(matching)!=1:raise ValueError(f'W41 candidate not unique: p{pg} issue {i}')
        row=matching[0]
        group={}
        for mode in ('price','advert'):
            name=f'p{pg:02d}-issue-{i:02d}-{mode}'
            path=paddle_dir/(name+'.json');crop=crops_dir/(name+'.png')
            if not path.is_file() or not crop.is_file() or path.is_symlink() or crop.is_symlink():raise ValueError(f'Missing real OCR/crop: {name}')
            doc=json.loads(path.read_text())
            if doc.get('file')!=name+'.png' or doc.get('page')!=pg or doc.get('issue')!=i or doc.get('mode')!=mode or doc.get('review_only') is not True:
                raise ValueError('Paddle provenance mismatch')
            if doc.get('image_sha256')!=hashlib.sha256(crop.read_bytes()).hexdigest():raise ValueError('Crop SHA-256 mismatch')
            width,height=png_size(crop)
            for item in doc['lines']:
                x0,y0,x1,y1=item['box']
                if not (0<=x0<x1<=width and 0<=y0<y1<=height):raise ValueError('Invalid crop bounding box')
            group[mode]=(doc, (width,height))
        outputs[(pg,i)]=(row,group)
    return outputs

def run_fusion(v2,vision,checked):
    predicted=copy.deepcopy(v2)
    predicted['schema']='nutrivalue_w41_ab_evidence_v3_paddle_review_only'
    predicted.pop('v2_reference_scoring_only',None)
    predicted['paddle_targeted_only']=True
    pages={r['page']:r for r in checked_vision(vision)}
    edits=[]
    for (pg,i),(row,group) in checked.items():
        target=next(r for r in predicted['candidates'] if r['page']==pg and r['prior']['text']==row['prior']['text'])
        expected_match=PRIOR_RE.search(row['prior']['text'].replace(' ','').upper())
        if not expected_match:raise ValueError('Expected textual previous price from Vision prior')
        expected=f'{int(expected_match[1])}.{expected_match[2]}'
        pair={mode:role_evidence(group[mode][0]['lines'],expected,mode) for mode in ('price','advert')}
        price=pair['price'];ad=pair['advert']
        # Both crop modes must read same FØR amount; keep every earlier conflict visible.
        if not price['prior'] or price['prior']!=ad['prior']:
            raise ValueError(f'Prior differs between two OCR crops: p{pg} issue {i}')
        v=price['current'];other=ad['current']
        if v and other and v!=other:
            v=None;state='crop_current_price_disagreement_review_only'
        elif v and other and v==other:state='dual_crop_spatial_ocr_candidate_review_only'
        elif v or other:
            v=v or other;state='single_crop_spatial_ocr_candidate_review_only'
        else:state='unresolved_spatial_price_review_only'
        original=copy.deepcopy(target.get('printed_current_ocr'))
        original_prior=copy.deepcopy(target['prior'])
        rescue={'page':pg,'issue':i,'previous_price':original,'previous_prior':original_prior,
                'paddle_prior':price['prior'],'price_crop':price,'advert_crop':ad,
                'suggested_current_nok':v,'status':state,'not_independent_product_identity':True}
        target['targeted_paddle_evidence']=rescue
        # These are proposals only, even when Vision and Tesseract previously disagree.
        # Preserve disagreement metadata, never alter original prior without retaining it.
        if v:
            geometry=price if price['current']==v and price.get('box') else ad
            mode='price' if geometry is price else 'advert'
            line_box=fullbox(geometry['box'],target['prior']['box'],mode,group[mode][1])
            target['printed_current_ocr']={'status':state,'value':v,'box':line_box,
                'source':'targeted_paddle_ocr_geometric_review_only',
                'prior_disagreement_preserved':original}
            rescue['current_full_page_box_bottom_origin']=line_box
        if target['prior']['kind']=='conflict':
            # Vision's prior was explicit; independent Paddle OCR agrees. Tesseract
            # red crop still DISAGREES and is preserved in original_prior above.
            if expected==price['prior']:
                target['prior']['amounts']=[price['prior']]
                target['prior']['kind']='paddle_vision_same_prior_conflict_review_only'
                rescue['prior_conflict_remains']=True
        peers=[x for x in predicted['candidates'] if x['page']==pg]
        target['nearby_ocr_caption_evidence']=product_caption_evidence(pages[pg]['lines'],target,peers)
        prior_state='ambiguous_prior_review_only' if target['prior']['kind'] in ('conflict','ambiguous') else 'ocr_price_caption_candidate_review_only'
        if v and target['nearby_ocr_caption_evidence'] and target['prior']['amounts']:
            target['status']='ocr_price_caption_candidate_review_only'
        elif v and target['prior']['amounts']:
            target['status']='ocr_price_no_caption_review_only'
        elif v:target['status']='ambiguous_prior_review_only'
        else:target['status']='ambiguous_price_review_only'
        edits.append(rescue)
    predicted['paddle_review_only_changes']=edits
    predicted['tjek_identity_verified']=False
    predicted['database_writes']=False
    predicted['historical_prices_verified']=False
    counts=Counter(r['status'] for r in predicted['candidates'])
    predicted['counts']={'prior_anchors':39,'not_matched_to_large_blue_plaque':sum(not r['blue_plaque_match'] for r in predicted['candidates']),**counts}
    return predicted

def main(argv=None):
    ap=argparse.ArgumentParser()
    for arg in ('vision','v2-evidence','paddle-dir','crops-dir','out-dir'):
        ap.add_argument('--'+arg,type=Path,required=True)
    ap.add_argument('--reference',type=Path)
    a=ap.parse_args(argv)
    if not private_path(a.out_dir) or a.out_dir.exists():ap.error('Output dir must be new and under data/private/')
    for p in (a.vision,a.v2_evidence,a.paddle_dir,a.crops_dir,a.reference):
        if p is not None and (not p.exists() or p.is_symlink()):ap.error('Missing or symlink input')
    try:
        vision_bytes=a.vision.read_bytes();vision=json.loads(vision_bytes)
        v2=json.loads(a.v2_evidence.read_text())
        if hashlib.sha256(vision_bytes).hexdigest()!=v2.get('v2_vision_sha256'):
            raise ValueError('Vision hash does not match V2 evidence')
        checked=validate_inputs(v2,vision,a.paddle_dir,a.crops_dir)
        out=run_fusion(v2,vision,checked)
        if a.reference:
            ref=json.loads(a.reference.read_text());validate_ref(ref)
            rows=reference_only_review_rows(out,ref)
            scoring=score(out,ref)
        else:rows=[];scoring=None
        out['v3_reference_scoring_only']=scoring
        out['v3_source_sha256']={'paddle_summary':hashlib.sha256((a.paddle_dir/'summary.json').read_bytes()).hexdigest(),
            'v2_evidence':hashlib.sha256(a.v2_evidence.read_bytes()).hexdigest(),
            'vision':hashlib.sha256(vision_bytes).hexdigest()}
    except (ValueError,KeyError,TypeError,IndexError,FileNotFoundError,json.JSONDecodeError) as e:
        ap.error('STOP: '+str(e)[:250])
    a.out_dir.mkdir(parents=True)
    (a.out_dir/'w41-ab-evidence-v3.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
    if rows:
        with (a.out_dir/'reference-only-review.csv').open('w',newline='') as fh:
            w=csv.DictWriter(fh,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    report=['# W41 — Targeted PP-OCRv5 to A/B V3 (review only)','',f"Candidates: {out['counts']}",
         'Reference (held aside; not proof of Tjek identity): '+str(scoring),'',
         '## Targeted proposals']
    for r in out['paddle_review_only_changes']:
        report.append(f"- p{r['page']:02d} issue {r['issue']:02d}: {r['paddle_prior']} → {r['suggested_current_nok'] or '?'}; {r['status']}")
    report+=['','No Tjek offers or SKU verification. Prior conflict retains both prior source readings.',
             'No new Vision/Paddle/LLM/network calls. No DB writes. All prices remain review-only.',
             '39 FØR-ads are not the whole 29-page flyer.']
    (a.out_dir/'audit.md').write_text('\n'.join(report)+'\n')
    print('\n'.join(report))

if __name__=='__main__':main()
