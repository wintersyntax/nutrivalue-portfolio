#!/usr/bin/env python3
"""REMA W40/W41 read-only holdout leakage preflight.

Uses only file SHA-256, local images (Pillow), and cached Tesseract TSVs.
Does not extract, mutate, train, fetch, call APIs, or score product accuracy.
"""
from __future__ import annotations
import argparse, csv, hashlib, html, json, statistics
from collections import Counter
from pathlib import Path
from PIL import Image, ImageChops, ImageStat

EXPECTED_W40=26
EXPECTED_W41=29
DIM=(64,96)

def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()

def read_manifest(path:Path)->dict[Path,str]:
    out={}
    for l in path.read_text(encoding='utf-8').splitlines():
        a,sep,b=l.partition('  ')
        if not sep or len(a)!=64:raise ValueError('Bad sha256 manifest row: '+l)
        p=Path(b)
        if p in out: raise ValueError('Duplicate path '+str(p))
        out[p]=a
    return out

def grayscale(path):
    with Image.open(path) as x:
        x.verify()
    with Image.open(path) as x:
        if x.size != (700,1040):raise ValueError(f'Unexpected image dimension {path.name}: {x.size}')
        return x.convert('L').resize(DIM, Image.Resampling.BILINEAR)

def image_mae(a,b):
    return float(ImageStat.Stat(ImageChops.difference(a,b)).mean[0])

def classify(exact,mae):
    if exact:return 'EXACT_SHA256'
    if mae < 1:return 'NEAR_PIXEL_MATCH_REVIEW'
    if mae < 15:return 'LAYOUT_OVERLAP_REVIEW'
    return 'PAGE_WIDE_DIFFERENCE_REVIEW'

def ocr_stats(tsv:Path,txt:Path):
    with tsv.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
        r=csv.DictReader(f,delimiter='\t')
        words=[row for row in r if row.get('level')=='5' and row.get('text','').strip()]
    values=[]
    for row in words:
        try:
            n=float(row.get('conf','-1'))
            if n>=0:values.append(n)
        except ValueError:pass
    t=txt.read_text(encoding='utf-8',errors='replace')
    return {'ocr_word_tokens':len(words),'ocr_median_conf':round(statistics.median(values),2) if values else None,
            'ocr_txt_chars':len(t.strip()),'ocr_digit_tokens':sum(any(c.isdigit() for c in z.get('text','')) for z in words)}

def html_report(rows,out:Path,images_w40:Path,images_w41:Path):
    tdir=out/'thumbs';tdir.mkdir(exist_ok=True)
    parts=['<!doctype html><html lang="hr"><meta charset="UTF-8"><title>W40 / W41 preflight</title>',
    '<style>body{font:15px system-ui,sans-serif;max-width:1250px;margin:25px auto;padding:0 15px;color:#182231}table{border-collapse:collapse;width:100%} td,th{border-bottom:1px solid #cad0db;padding:10px;vertical-align:top;text-align:left}img{width:205px;border:1px solid #cbd5e1}small{color:#526079}th{position:sticky;top:0;background:#fff}details{margin:20px 0}</style>',
    '<h1>W40 ↔ W41 — offline kontrola preklapanja</h1>',
    '<p>Ovaj izvještaj NE mjeri točnost izdvajanja proizvoda ni cijena. Uspoređuje već spremljene slike i lokalni OCR. Isti SHA-256 znači byte-identičnu stranicu; sličan izgled nije potvrda jednakosti oglasa.</p>',
    '<table><thead><tr><th>W40</th><th>Najbliža W41</th><th>Status</th><th>Razlika (MAE)</th><th>Slike</th></tr></thead><tbody>']
    def thumb(img,filename):
        p=tdir/filename
        with Image.open(img) as im:
            im.convert('RGB').resize((175,260),Image.Resampling.LANCZOS).save(p,quality=83)
        return f'thumbs/{p.name}'
    w41thumb={}
    for row in rows:
        p=int(row['w40_page']);q=int(row['closest_w41_page'])
        x=thumb(images_w40/f'page-{p:02d}.jpg',f'w40-{p:02d}.jpg')
        if q not in w41thumb:w41thumb[q]=thumb(images_w41/f'page-{q:02d}.jpg',f'w41-{q:02d}.jpg')
        y=w41thumb[q]
        parts.append(f'<tr><td>{p:02d}<br><small>{row["ocr_word_tokens"]} OCR riječi</small></td><td>{q:02d}</td>'
                     f'<td><code>{html.escape(row["match_class"])}</code></td><td>{row["grayscale_mae_64x96"]}</td>'
                     f'<td><img alt="W40 str. {p}" src="{x}"> <img alt="W41 str. {q}" src="{y}"></td></tr>')
    parts.append('</tbody></table><p>Sve su slike smanjeni pregledi; za konačnu procjenu ponuda koristi originalne kataloge. Niti jedan status ne daje službenu metriku kvalitete parsera.</p></html>')
    (out/'review-overlap.html').write_text('\n'.join(parts),encoding='utf-8')

def run(w40,w41,ocr,manifest,frozen_manifest,v5,out):
    out.mkdir(parents=True,exist_ok=True)
    a={i:w40/f'page-{i:02d}.jpg' for i in range(1,EXPECTED_W40+1)}
    b={i:w41/f'page-{i:02d}.jpg' for i in range(1,EXPECTED_W41+1)}
    if any(not p.exists() for p in [*a.values(),*b.values()]): raise FileNotFoundError('Missing W40 or W41 image')
    extra_a=list(w40.glob('page-*.jpg'));extra_b=list(w41.glob('page-*.jpg'))
    if len(extra_a)!=26 or len(extra_b)!=29:raise ValueError(f'Unexpected image count W40={len(extra_a)} W41={len(extra_b)}')
    man=read_manifest(manifest)
    if len(man)!=26:raise ValueError(f'Manifest length {len(man)} !=26')
    failures=[]
    for i,path in a.items():
        rel=next((k for k in man if k.name==path.name and '2026-W40' in str(k)),None)
        if rel is None or sha(path)!=man[rel]:failures.append(path.name)
    if failures:raise ValueError('W40 SHA mismatches '+str(failures))
    frozen=read_manifest(frozen_manifest)
    if len(frozen)!=1 or sha(v5)!=next(iter(frozen.values())):raise ValueError('Frozen V5 SHA256 mismatch')
    for i in a:
        for ext in ('tsv','txt'):
            if not (ocr/f'page-{i:02d}.{ext}').is_file():raise FileNotFoundError(f'W40 OCR missing page-{i:02d}.{ext}')
    hashes_b={i:sha(path) for i,path in b.items()}
    hashes_a={i:sha(path) for i,path in a.items()}
    im_b={i:grayscale(path) for i,path in b.items()}
    records=[]
    for i,path in a.items():
        picture=grayscale(path)
        best=min(((image_mae(picture,g),j) for j,g in im_b.items()),key=lambda x:x[0])
        mae,j=best
        exacts=[z for z,h in hashes_b.items() if h==hashes_a[i]]
        if exacts and j not in exacts:j=exacts[0];mae=0.0
        row={'w40_page':i,'closest_w41_page':j,'match_class':classify(bool(exacts),mae),
            'sha256_exact':bool(exacts),'grayscale_mae_64x96':round(mae,3),
            'w40_sha256':hashes_a[i],'w41_sha256':hashes_b[j]}
        row.update(ocr_stats(ocr/f'page-{i:02d}.tsv',ocr/f'page-{i:02d}.txt'))
        records.append(row)
    with (out/'page-pairs.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,records[0].keys());writer.writeheader();writer.writerows(records)
    html_report(records,out,w40,w41)
    count=Counter(r['match_class'] for r in records)
    summary={'schema':'nutrivalue_w40_w41_holdout_overlap_audit_v1','method':'SHA256 exact + local grayscale downsampled MAE',
        'w40_pages':26,'w41_pages':29,'w40_image_hashes_verified':26,'frozen_v5_hash_verified':True,
        'ocr_tsv_files':26,'ocr_txt_files':26,'page_overlap_classes':dict(count),
        'exact_sha256_pages':count['EXACT_SHA256'],
        'holdout_status':'CONTAMINATED_NOT_INDEPENDENT',
        'independent_quality_metric':'NOT_MEASURED',
        'parser_v5_executed':False,
        'reason':'Original 26 W40 pages contain 16 byte-identical W41 pages; W41 trained/rule-designed source overlaps the test target.',
        'warnings':['Near pixels do not guarantee identical offers, names or prices.',
         'Tesseract OCR on W40 cannot be directly compared to Google Vision-assisted W41 extraction quality.',
         'W41 V5 is a title refiner requiring W41 V4-like candidates, not a complete standalone price detector.',
         'No source data, parser code, DB, Tjek identities, or published prices changed.']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    md=["# REMA 1000 W40 — provjera kontaminacije holdout testa",'',
        '**Status: CONTAMINATED_NOT_INDEPENDENT**. Ovo NIJE test stvarne točnosti parsera.','',
        f'- W40: **{len(a)}** originalnih JPG stranica, **26/26** SHA256 podudara spremljeni W40 manifest.',
        f'- W41: **{len(b)}** originalnih JPG stranica dostupno za usporedbu.',
        f'- Zamrznuta V5 skripta: **SHA256 odgovara** predanom W40 manifestu.',
        f'- W40 OCR datoteke: **26/26 TSV** i **26/26 TXT**.',
        f'- **{count["EXACT_SHA256"]}/26** stranica W40 byte-identično je stranici W41.',
        f'- **{count["NEAR_PIXEL_MATCH_REVIEW"]}/26** dodatnih stranica imaju gotovo identičan vizualni prikaz (MAE<1, nisu bit-identične).',
        f'- **{count["LAYOUT_OVERLAP_REVIEW"]}/26** dodatnih stranica imaju značajno preklapanje rasporeda (1<=MAE<15; može sadržavati drugačije oglase).',
        f'- **{count["PAGE_WIDE_DIFFERENCE_REVIEW"]}/26** stranica značajnije se razlikuju od najbliže W41 stranice.',
        '', '## Najvažnije posljedice','',
        'W40 nije valjani nezavisni **whole-catalog holdout** za V5 budući da je V5 razvijan uz W41 slike i ponude. ',
        'Rezultati poput 90% ili 92% iz W41 preliminarne AI reference **ne mogu se validirati** ovim W40 skupom.',
        'Na izmijenjenim stranicama pojedinačna područja mogu biti nova čak ako je većina slike ista. ',
        'Takva područja mogu se izdvojiti za zasebni tehnički smoke-test tek nakon ručnog označavanja i provjere da nisu već u W41.',
        '', '## Evidencija po stranicama','',
        '| W40 | najbliža W41 | status | MAE | OCR riječi |', '|---:|---:|---|---:|---:|']
    for r in records:
        md.append(f'| {r["w40_page"]} | {r["closest_w41_page"]} | `{r["match_class"]}` | {r["grayscale_mae_64x96"]} | {r["ocr_word_tokens"]} |')
    md.extend(['','## Metodologija i granice','',
       '- **SHA256**: isti hash istih JPG bajtova, dokaz identičnog materijala.',
       '- **MAE**: samo gruba razlika grayscale slika smanjenih na 64x96. Pragovi su **trijaža**, ne zaključak o identičnosti oglasa.',
       '- Podaci su u `page-pairs.csv`; pregledni prikaz u `review-overlap.html`.',
       '- Nema neovisnog human-ground-truth, izmjerenog recall-a, novih OCR poziva, Google/Paddle/Tjek/LLM pristupa, DB upisa ni izmjene V5.',
       '- **Preporuka**: odabrati drugi katalog/tjedan nakon SHA/perceptual prefligha; W40 zadržati samo kao regression/provenance primjer.',
    ])
    (out/'audit.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    print('W40-W41 overlap status:',summary['holdout_status'])
    print('Exact W40 pages:',summary['exact_sha256_pages'],'of 26')
    print('Class counts:',dict(count))
    print('Report:',out/'audit.md')
    return summary,records

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for key in ('w40','w41','ocr','manifest','frozen-manifest','v5','out-dir'):ap.add_argument('--'+key,required=True,type=Path)
    a=ap.parse_args()
    run(a.w40,a.w41,a.ocr,a.manifest,a.frozen_manifest,a.v5,a.out_dir)

if __name__=='__main__':main()
