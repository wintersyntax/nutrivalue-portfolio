#!/usr/bin/env python3
"""Offline conservative product-title refinement: V4 evidence -> V5 proposals.

Independent visual/AI reference labels must NEVER be passed to inference.  Every
new proposal is review-only, original OCR fields untouched; no network/DB/OCR.
"""
from __future__ import annotations
import argparse,csv,copy,html,json,re,hashlib,unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

SCHEMA = 'nutrivalue_w41_full_catalog_title_refinement_v5_review_only'
NON_ITEM = {'COMPOSITE_TOTAL','GRAPHIC_NOT_OFFER'}
GENERIC = {'PR','STK','FØR','FOR','PANT','REMA','TIPS','LAST','PRIS','BONUS','ALLTID', 'LAVE','UTEN'}
FOOTER_UNWANTED = (' PR. KG', ' PR KG',' PR.STK',' PR STK',' PR. L', ' PR L')


def norm(s):
    s=str(s or '').upper().translate(str.maketrans('ØÆÅÉ','OAAE'))
    return re.sub('[^A-Z0-9]+','',s)

def tokens(s):
    return re.findall(r'[A-ZÆØÅ]{3,}|\d+\s*PK|\d+\s*KG',str(s or '').upper())

def cbox(b):
    b=b or {}; return float(b.get('x',0))+.5*float(b.get('width',0)),float(b.get('y',0))+.5*float(b.get('height',0))

def dup_token_fix(s):
    """Repair repeated OCR syllable in *long* words, not genuine repetitions."""
    def repair(match):
        word=match.group()
        if len(word)<10:return word
        for n in (4,3):
            if word[:n]==word[n:2*n] and len(word)-n>=7:
                return word[n:]
            # A repeated suffix inside a longer compound, e.g. ...BRØDBRØD.
            if len(word)>2*n and word[-2*n:-n]==word[-n:]:
                return word[:-n]
        return word
    return re.sub(r'\b[A-ZÆØÅ]{10,}\b',repair,s)

def clean_caption(s):
    s=' '.join(str(s or '').split()).strip(' ,;:')
    # Separate uppercase printed product title from following mixed-case
    # supplier/package/unit-price prose.  Never infer unstated product names.
    parts=re.split(r'\s+[A-ZÆØÅa-zæøå][a-zæøå]{2,}(?=\s*,|\s+\d+[.,-]?\d*\s*(?:g|kg|ml|l)\b)',s,maxsplit=1)
    if len(parts)>1 and len(parts[0])>=5: s=parts[0]
    if re.search(r'\b(?:pr\s*\.?\s*(?:kg|l|stk))\b',s,re.I):
        # A line that consists primarily of producer and unit-rate is not a title.
        s=re.split(r',\s*\d|\s+\d+[.,]\d+\s*pr',s,maxsplit=1,flags=re.I)[0]
    return dup_token_fix(s).strip(' ,;:')

def plausible_title(s):
    t=norm(s)
    if any(v in s.upper() for v in ('UTVALGET KAN VARIERE','TIPS !','PITABRØD KAN','SKANN FOR','SE ETTER')):return False
    return len(t)>=5 and t not in GENERIC and not re.fullmatch(r'\d+(?:FOR|PK|KG)?',t) and not s.upper().strip().startswith(('+ PANT','PR . STK','PR. STK','ALLTID LAVE', 'PANT'))

def local_page(ocr,p):
    if ocr is None: return []
    pages=ocr.get('pages',[])
    for z in pages:
        if int(z.get('page',-1))==p: return z.get('lines',[])
    return []

def candidates_for(c,ocr):
    options=[]; bprice=c.get('price_box') or {};px,py=cbox(bprice)
    def add(s,box,source,idx=None):
        st=clean_caption(s)
        if not plausible_title(st):return
        x,y=cbox(box)
        if not box or abs(x-px)>.38 or (y>py+.045 or py-y>.61):return
        # Require the printed title is lower on the page than the price or close
        # to it, since this catalogue's OCR y increases upwards.
        options.append(dict(text=st,box=box,source=source,line_index=idx, dx=round(abs(x-px),3),dy=round(py-y,3),raw=s))
    for z in c.get('caption_candidates',[]):add(z.get('text',''),z.get('box'),'vision_caption',z.get('ocr_line_index'))
    for z in c.get('title_evidence_v2',[]):add(z.get('clean_title') or z.get('text',''),z.get('box'),'v2_ranked',z.get('ocr_line_index'))
    for i,z in enumerate(local_page(ocr,int(c['page']))):
        # OCR of the original full-page image is permitted as fallback evidence.
        add(z.get('text',''),z.get('box'),'cached_tesseract',i)
    seen=set();out=[]
    for z in options:
        key=norm(z['text'])
        if key and key not in seen:
            seen.add(key);out.append(z)
    return out

def pick_title(c,ocr):
    if c.get('pricing_mode') in NON_ITEM:return '', 'NON_ITEM_PRESERVED',[],False
    old=(c.get('guarded_product_title_v3') or '').strip()
    opts=candidates_for(c,ocr)
    px,py=cbox(c.get('price_box'))
    # A page-wide printed footer may describe several advertised variants with a
    # single price.  Require lexical overlap with existing OCR titles, except
    # when no title was previously found.
    for z in opts:
        u=z['text'].upper();orig=old.upper();z['score']=0.0
        z['score']+=max(0, .32-z['dx'])*3 + max(0,.25-abs(z['dy']-.11))*2
        if z['source']=='vision_caption':z['score']+=.45
        if z['source']=='cached_tesseract':z['score']-=.3
        if u.startswith('UTVALGT ') or u.startswith('UTVALGTE '):z['score']+=1.1
        if len(norm(z['text']))>=len(norm(old))+7:z['score']+=.7
        if len(tokens(z['text']))>=2:z['score']+=.25
        if z['dy']>0.045:z['score']+=.3
        # Package print is often close to price with tall text. Footer titles
        # are normally small and farther down the page.
        if z['box']['height']<.04 and z['dy']>.025:z['score']+=.35
    if not old:
        # Avoid choosing generic/marketing headline from an unrelated advert.
        good=[z for z in opts if z['source']=='cached_tesseract' and z['dy']>=.075 and len(norm(z['text']))>=9 and z['box']['height']<.04]
        if len(good)==1:
            x=good[0];return x['text'],'MISSING_TITLE_FROM_CACHED_FOOTER', [x],True
        return old,'NO_SAFE_TITLE_EVIDENCE',opts,False
    repaired=dup_token_fix(old)
    if repaired!=old:
        return repaired,'MECHANICAL_REPEATED_OCR_SYLLABLE',[],True
    def matches(a,b):
        aa,bb=norm(a),norm(b)
        if len(aa)<4 or len(bb)<4:return False
        return aa in bb or bb in aa or SequenceMatcher(None,aa,bb).ratio()>=.72
    footer=[z for z in opts if z['dy']>=.042 and z['box']['height']<.04]
    # Group variant line containing slashes must be lexically anchored in the
    # already-detected group, never borrowed from a neighboring price cell.
    for z in footer:
        if z['text'].count('/')<2:continue
        keywords=[t for t in tokens(old) if len(t)>=4]
        parts=[q for q in re.split(r'[/\\,]',z['text']) if len(norm(q))>=4]
        if not any(any(norm(k) in norm(q) or SequenceMatcher(None,norm(k),norm(q)).ratio()>=.79 for q in parts) for k in keywords):continue
        if z['dx']>.34:continue
        group=z['text']
        # Repair a truncated last group variant only when another local OCR
        # caption provides the complete spelling independently.
        for part in group.split('/'):
            if part.endswith(('!','|')) and SequenceMatcher(None,norm(part[:-1]),norm(old)).ratio()>=.85:
                group=group.replace(part,old.upper())
        return group,'SAME_GROUP_ALL_VARIANTS_FROM_FOOTER', [z],True
    # Correct a same-length package OCR typo only when two cached OCR engines
    # independently show the alternative at the same printed footer location.
    # Geometry + second text observation are both mandatory.
    if len(tokens(old))==1 and len(norm(old))>=5:
        for z in footer:
            if z['source']!='vision_caption' or abs(len(norm(z['text']))-len(norm(old)))>1:continue
            if SequenceMatcher(None,norm(old),norm(z['text'])).ratio()<.73:continue
            for v in local_page(ocr,int(c['page'])):
                b=v.get('box') or {}
                if norm(v.get('text'))==norm(z['text']) and abs(cbox(b)[0]-cbox(z['box'])[0])<.07 and abs(cbox(b)[1]-cbox(z['box'])[1])<.035:
                    return z['text'],'TWO_CACHED_OCR_FOOTER_SPELLING_AGREEMENT',[z],True
    # A longer footer that preserves the earlier title's product-family prefix
    # can safely replace a truncated package name.
    ups=[]
    for z in footer:
        a,b=norm(old),norm(z['text'])
        if len(b)<len(a)+2:continue
        if any(x.islower() for x in z['text']) and old.upper()==old and norm(old) in norm(z['text']):continue
        if not z['text'].isupper() and re.search(r'\b(?:pr\s*\.?\s*(?:kg|l|stk))\b',z['raw'],re.I):continue
        # Do not append producer/unit-price fragments or replace a distinct
        # pack size/type (e.g. 2PK vs GLUTENFRI next to another price).
        if re.search(r'\b(?:BAMA|REMA\s*1000|Dr\.?\s*Oetker|Gode\s+hav)\b',z['text'],re.I) and not re.search(r'\b(?:BAMA|REMA\s*1000|Dr\.?\s*Oetker|Gode\s+hav)\b',old,re.I):continue
        exact_prefix=a in b
        # If OCR damaged the original title's spelling, compare just the
        # longer primary product token; still insist on a footer title.
        similar_first=(len(tokens(old))==1 and len(a)>=9 and any(SequenceMatcher(None,a,norm(q)).ratio()>=.78 for q in re.split(r'[/\s]',z['text']) if len(norm(q))>=6))
        if exact_prefix or similar_first:
            ups.append(z)
    ups=sorted(ups,key=lambda z:(len(norm(z['text']))-len(norm(old))>4,z['score'],len(z['text'])),reverse=True)
    if ups:
        z=ups[0];return z['text'],'EXTENDED_SAME_PRODUCT_FOOTER', [z],True
    # Printed footers with an explicit selectable product category take
    # precedence over ambiguous package variety/producer labels. Restrict to
    # very local, credible Vision evidence and guard against neighboring cards.
    explicit=[z for z in footer if z['source'] in ('vision_caption','v2_ranked') and re.match(r'^(?:UTVALGT|UTVALGTE)\s+',z['text'].upper()) and z['dx']<.25]
    if explicit:
        z=max(explicit,key=lambda v:v['score']); return z['text'],'CATEGORY_FOOTER_OVER_PACK_VARIANT',[z],True
    # A one-word OCR title may be a package brand.  Prefer a multiword footer
    # sharing at least one meaningful term, but do not invent missing suffixes.
    if len(tokens(old))<2 or len(norm(old))<9:
        lst=[z for z in footer if len(norm(z['text']))>=len(norm(old))+5 and z['dx']<.28 and (matches(old,z['text']) or (z['source']=='vision_caption' and len(tokens(z['text']))>=2 and z['dy']>.09 and sum(ch.isupper() for ch in z['text'])/max(1,sum(ch.isalpha() for ch in z['text']))>.60 and not re.search(r'\b(?:pr\s*\.?\s*(?:kg|l|stk))\b',z['raw'],re.I)))]
        if lst:
            z=max(lst,key=lambda v:v['score']);return z['text'],'WEAK_PACK_LABEL_REPLACED_BY_FOOTER',[z],True
    # OCR may capture long unrelated text on the package while a clean,
    # explicitly printed label appears in a nearby footer. Require standalone
    # uppercase text with title-like position, never supplier/unit-rate prose.
    if len(norm(old))>=15:
        labels=[z for z in footer if z['source']=='vision_caption' and .04<=z['dy']<=.24 and z['dx']<.22
                and len(norm(z['text']))>=6 and len(norm(z['text']))<len(norm(old))
                and z['raw'].strip().upper()==z['raw'].strip() and 'PR.' not in z['raw'].upper()
                and z['box']['height']<.035]
        # Single-word category must lexically resemble a distorted package
        # token. Two-word partial neighbors are much more likely contamination.
        labels=[z for z in labels if len(tokens(z['text']))==1 and any(SequenceMatcher(None,norm(z['text']),norm(t)).ratio()>=.49 for t in tokens(old))]
        if labels and any(len(t)>10 for t in tokens(old)) and len(tokens(old))<=2:
            z=max(labels,key=lambda z:z['score']);return z['text'],'GARBLED_PACK_TEXT_REPLACED_BY_STANDALONE_FOOTER',[z],True
    # A multibuy plaque may cover several flavors/brands. Expand only when
    # neighboring OCR independently repeats at least two extra labels in the
    # same printed offer row. Never infer any prices per bottle.
    if c.get('pricing_mode')=='MULTIBUY_CONDITIONAL' and len(tokens(old))==1:
        group=[]
        for i,z in enumerate(local_page(ocr,int(c['page']))):
            b=z.get('box') or {};x,y=cbox(b)
            if not (.05<=py-y<=.18 and abs(x-px)<.32):continue
            textline=str(z.get('text','')).upper()
            # OCR may attach stray single characters before repeated brands.
            words=re.findall(r'(?<![A-ZÆØÅ])[A-ZÆØÅ]{4,9}(?![A-ZÆØÅ])',textline)
            found=[]
            for w in set(words):
                if words.count(w)>=2 and w not in GENERIC and len(w)<8:
                    found.append(w)
            if len(found)>=2:
                group.append((i,b,sorted(found),textline))
        if len(group)==1 and all(norm(t) not in norm(old) for t in group[0][2]):
            i,b,names,raw=group[0]
            new=' / '.join([old]+names)
            ev=dict(text=new,raw=raw,source='cached_tesseract_repeated_brand_group',box=b,line_index=i)
            return new,'MULTIBUY_VARIANTS_FROM_REPEATED_OCR_LABELS',[ev],True
    # Preserve when the geometry or product similarity is too uncertain.
    return old,'UNRESOLVED_KEEP_V4',[],False

def refine(c,ocr):
    new=copy.deepcopy(c)
    name,reason,evidence,chg=pick_title(c,ocr)
    old=c.get('guarded_product_title_v3') or ''
    new['proposed_product_title_v5']=name
    new['title_selection_reason_v5']=reason
    new['title_changed_v5']=bool(name!=old)
    new['title_evidence_v5']=[dict(source=z['source'],text=z['text'],raw_ocr=z['raw'],box=z['box'],line_index=z['line_index']) for z in evidence]
    # If no safe category can be found, preserve the field but surface risk.
    new['title_decision_review_only_v5']=True
    if c.get('guard_reason_v3')=='UNRESOLVED_OR_GENERIC_TITLE_REVIEW_ONLY' and not new['title_changed_v5']:
        new['title_review_warning_v5']='UNRESOLVED_BRAND_OR_GROUP_SCOPE'
    if c.get('price_context_warning')=='OCR_UNIT_RATE_DISAGREES_REVIEW_ONLY':
        ev=c.get('nearby_unit_rate_evidence') or {}
        if ev.get('comparable_per_weight') and ev.get('differs_from_printed_ocr'):
            new['price_alternative_nok_v5']=ev.get('value_nok')
            new['price_conflict_status_v5']='PRINTED_OCR_VS_COMPARABLE_UNIT_PRICE_REVIEW_ONLY'
            new['price_evidence_v5']=[dict(source='cached_vision_unit_rate',raw=ev.get('raw_text'),line_index=ev.get('line_index'),candidate_nok=ev.get('value_nok'))]
    # Never overwrite printed_current_nok or accept a Paddle-only price.
    assert new.get('printed_current_nok')==c.get('printed_current_nok')
    assert new.get('targeted_paddle_price_candidate_nok_v4')==c.get('targeted_paddle_price_candidate_nok_v4')
    assert new.get('publishable',False) is False
    return new

def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument('--v4',type=Path,required=True)
    ap.add_argument('--cached-ocr',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    args=ap.parse_args(argv)
    if args.out_dir.exists():ap.error('Refusing to overwrite existing output folder')
    v4=json.loads(args.v4.read_text(encoding='utf-8'))
    ocr=json.loads(args.cached_ocr.read_text(encoding='utf-8'))
    if v4.get('week')!='2026-W41' or v4.get('pages')!=29 or len(v4.get('candidates',[]))!=104:ap.error('Unexpected W41 V4 input identity')
    if ocr.get('week')!='2026-W41' or ocr.get('page_count')!=29:ap.error('Unexpected local OCR input identity')
    ids=[z['candidate_id'] for z in v4['candidates']]
    if len(set(ids))!=104:ap.error('Duplicate candidate IDs')
    args.out_dir.mkdir(parents=True)
    out=copy.deepcopy(v4);out['schema']=SCHEMA;out['candidate_input_sha256']=hashlib.sha256(args.v4.read_bytes()).hexdigest();out['cached_ocr_input_sha256']=hashlib.sha256(args.cached_ocr.read_bytes()).hexdigest()
    out['candidates']=[refine(c,ocr) for c in v4['candidates']]
    delta=[]
    for o,n in zip(v4['candidates'],out['candidates']):
        if n['title_changed_v5'] or n.get('price_conflict_status_v5'):
            delta.append(dict(candidate_id=o['candidate_id'],page=o['page'],old_title=o.get('guarded_product_title_v3',''),v5_title=n['proposed_product_title_v5'],reason=n['title_selection_reason_v5'],price_v4=o.get('printed_current_nok') or '',alternative_price_review_only=n.get('price_alternative_nok_v5') or '',source=(n.get('title_evidence_v5') or [{}])[0].get('source','')))
    (args.out_dir/'candidates-v5.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    keys=['candidate_id','page','old_title','v5_title','reason','price_v4','alternative_price_review_only','source']
    with (args.out_dir/'title-changes-v5.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(delta)
    reasons=Counter(c['title_selection_reason_v5'] for c in out['candidates'])
    changed=sum(c['title_changed_v5'] for c in out['candidates'])
    report=['# W41 V5 — offline candidate-title refinement (REVIEW ONLY)','','Original V4 candidates: 104; V5 candidates: 104.','Names changed: **%d**; no item price or accepted price overwritten.'%changed, '', '## Title-decision reasons']
    report += [f'- {k}: {v}' for k,v in sorted(reasons.items())]
    report += ['','## Changed titles / conflict evidence']
    report += [f"- {d['candidate_id']}: `{d['old_title']}` → `{d['v5_title']}` ({d['reason']})"+(f"; alternate price **{d['alternative_price_review_only']} NOK** UNACCEPTED" if d['alternative_price_review_only'] else '') for d in delta]
    report += ['','No human signoffs; no independent W41 ground truth, price acceptance, production writes, Tjek matching, LLM or OCR/API calls.','Official W41 full-catalog recall: **NOT_MEASURED_REVIEW_INCOMPLETE**.']
    (args.out_dir/'audit.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print('\n'.join(report[:5])); print('Review: '+str(args.out_dir/'title-changes-v5.csv'))

if __name__=='__main__':main()
