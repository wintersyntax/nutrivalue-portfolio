#!/usr/bin/env python3
"""Read-only, offline 2026-W43 Tjek vs 2026-W41 Priser24 visual audit.

python w43_overlap_audit.py --w43 nutrivalue-w43-images.zip \
   --w41 nutrivalue-w41-evaluation-input.zip --output ./w43-audit-output

No HTTP, OCR, external APIs or database writes. Human preview labels are NOT ground truth.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import html
import io
import json
import re
import zipfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

PAGE_RE = re.compile(r'(?:^|/)page-(\d{2})\.(?:jpe?g|png|webp)$', re.I)
EXPECTED = {"W43": 25, "W41": 29}

# Manually flagged after a contact-sheet comparison. Campaign, product, or
# price similarities need item-by-item ground-truth review before scoring.
# Labels are indicative and conservative, NOT an offer-level verdict.
VISUAL_REUSE = {
    1: (1, 'Vidljiv ponovljeni vizual kampanje -20% i proizvoda; donji dio promijenjen.'),
    4: (12, 'Žuti pileći proizvod i cijena 139,90 ponavljaju se na W41 p12; raspored je nov.'),
    5: (11, 'Ponovljena kampanja -20% ribljih proizvoda / srodna montaža.'),
    14: (17, 'Ponovljena slika kruha s tiskanom cijenom 29,90; W43 izdvaja proizvod.'),
    16: (17, 'Ponovljena ponuda kruha 29,90, W43 ima dodatne nove proizvode.'),
    18: (19, 'Gotovo isti oglas smrznutog kruha 29,90; W41 ima dodatnu oznaku prethodne cijene.'),
    20: (20, 'Dio proizvoda i cijena se ponavlja; ostatak stranice razlikuje se.'),
    24: (25, 'Vizualno gotovo identična Halloween posuda s cijenom 9,90.'),
}
# Use content-relevant pairs for review even where layout-only ORB matches
# another page more strongly. Pairings do NOT certify identity.
PAIR_OVERRIDES = {14: 17, 16: 17, 5: 11, 25: 18, 22: 26, 23: 25}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pages(zip_path: Path, week: str) -> dict[int, dict]:
    if not zip_path.is_file():
        raise FileNotFoundError(zip_path)
    result = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            m = PAGE_RE.search(info.filename)
            if not m:
                continue
            number = int(m.group(1))
            if number in result:
                raise ValueError(f'{week}: duplicated page index {number}')
            if info.file_size > 15_000_000 or info.file_size < 500:
                raise ValueError(f'{week}: invalid page size {number}')
            b = z.read(info)
            im = Image.open(io.BytesIO(b)).convert('RGB')
            if not 300 < im.width <= 4000 or not 400 < im.height <= 6000:
                raise ValueError(f'{week}: unexpected image dimensions {number}')
            gray = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2GRAY)
            result[number] = {
                'sha256': sha(b), 'size': [im.width, im.height], 'image': im,
                'gray64': cv2.resize(gray, (64, 96), interpolation=cv2.INTER_AREA),
                'gray224': cv2.resize(gray, (224, 332), interpolation=cv2.INTER_AREA),
                'gray350': cv2.resize(gray, (350, 520), interpolation=cv2.INTER_AREA),
            }
    if len(result) != EXPECTED[week] or sorted(result) != list(range(1, EXPECTED[week]+1)):
        raise ValueError(f'{week}: missing or wrong page numbers: {sorted(result)}')
    return result


def compare_gray(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    a = a.astype(np.float32).ravel()
    b = b.astype(np.float32).ravel()
    mae = float(np.mean(np.abs(a-b)))
    std = np.std(a)*np.std(b)
    corr = float(np.mean((a - np.mean(a))*(b - np.mean(b)))/std) if std else 0.
    return round(mae, 3), round(corr, 4)


def orb_pairs(a: dict, b: dict) -> tuple[int, int]:
    kp_a, des_a = a['orb']
    kp_b, des_b = b['orb']
    if des_a is None or des_b is None or len(des_b)<2:
        return 0, 0
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = matcher.knnMatch(des_a, des_b, k=2)
    good = [m for pair in matches if len(pair)==2 for m,n in [pair] if m.distance < .75*n.distance]
    inliers = 0
    if len(good)>=5:
        x = np.float32([kp_a[m.queryIdx].pt for m in good])
        y = np.float32([kp_b[m.trainIdx].pt for m in good])
        # Matches through repeated logos/decorations must NOT be interpreted
        # as a count of identical offers.
        _, mask = cv2.findHomography(x,y,cv2.RANSAC,5.0)
        if mask is not None:
            inliers = int(mask.sum())
    return inliers, len(good)


def generate(w43zip: Path, w41zip: Path, out: Path) -> dict:
    cv2.setNumThreads(1)
    w43 = load_pages(w43zip, 'W43')
    w41 = load_pages(w41zip, 'W41')
    orb = cv2.ORB_create(nfeatures=450,fastThreshold=12)
    for collection in (w41,w43):
        for row in collection.values():
            row['orb'] = orb.detectAndCompute(row['gray350'],None)
    matrix = {}
    for i,a in w43.items():
        for j,b in w41.items():
            low, corr = compare_gray(a['gray64'],b['gray64'])
            med, _ = compare_gray(a['gray224'],b['gray224'])
            ni, ng = orb_pairs(a,b)
            matrix[i,j] = {
                'mae64': low, 'mae224': med, 'corr64': corr,
                'orb_inliers': ni, 'orb_good_matches': ng,
                'sha_equal': a['sha256']==b['sha256'],
            }
    out.mkdir(parents=True,exist_ok=True)
    image_dir = out / 'previews'
    image_dir.mkdir(exist_ok=True)
    for week,collection in [('W43',w43),('W41',w41)]:
        for i,row in collection.items():
            im=row['image'].copy()
            im.thumbnail((600,892),Image.Resampling.LANCZOS)
            im.save(image_dir / f'{week.lower()}-{i:02d}.jpg', quality=83, optimize=True)
    rows=[]
    for i,a in w43.items():
        pixel_best = min(w41,key=lambda j: matrix[i,j]['mae64'])
        feature_best = max(w41,key=lambda j: (matrix[i,j]['orb_inliers'], -matrix[i,j]['mae64']))
        review_match = (VISUAL_REUSE.get(i) or (PAIR_OVERRIDES[i],) if i in PAIR_OVERRIDES and i not in VISUAL_REUSE else VISUAL_REUSE.get(i))
        match = review_match[0] if review_match else feature_best
        comparison = matrix[i,match]
        flagged=i in VISUAL_REUSE
        rows.append({
            'w43_page': i, 'w41_review_page': match,
            'w41_best_pixel': pixel_best, 'w41_best_feature': feature_best,
            'sha256_identical': comparison['sha_equal'],
            'gray_mae_64': comparison['mae64'],
            'gray_mae_224': comparison['mae224'],
            'gray_correlation_64': comparison['corr64'],
            'orb_inliers': comparison['orb_inliers'],
            'orb_good_matches': comparison['orb_good_matches'],
            'preview_flag': 'VISIBLE_REUSED_CAMPAIGN_OR_OFFER' if flagged else 'NO_CLEAR_REUSE_ESTABLISHED_FROM_PREVIEW',
            'review_note': VISUAL_REUSE[i][1] if flagged else 'Usporediti proizvode/cijene pojedinačno; izgled i ORB nisu dovoljan dokaz neovisnosti.',
        })
    csv_path = out / 'page-pairs.csv'
    with csv_path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f, fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    exact=sum(v['sha_equal'] for v in matrix.values())
    flag_count=sum(r['preview_flag'].startswith('VISIBLE') for r in rows)
    summary={
        'status': 'PARTIAL_OVERLAP_NEEDS_OFFER_LEVEL_GROUND_TRUTH',
        'sources': {'w43':str(w43zip.name),'w41':str(w41zip.name)},
        'pages': {'w43':25,'w41':29},
        'source_resolution': {'w43':list(w43[1]['size']), 'w41':list(w41[1]['size'])},
        'byte_identical_pairs':exact,
        'pages_with_obvious_visual_reuse_in_review':flag_count,
        'pages_flagged':sorted(VISUAL_REUSE),
        'tjek_advertised_offer_count_w43':98,
        'independent_product_plus_price_accuracy': 'NOT_MEASURED',
        'holdout_rule': 'Exclude or annotate all reused offers (not just matching whole pages) before score.',
        'warning':'No formal per-offer matching/annotation was performed. Metrics detect visual similarity only.',
    }
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    create_html(rows,out)
    audit=f'''# NutriValue — REMA W43 ↔ W41 preflight (offline)

**Zaključak: DJELOMIČNO PREKLAPANJE.** W43 je sadržajno različit od W41, ali postoji prepoznatljivo ponovno korištenje proizvoda, cijena ili kampanja. **W43 se ne smije u cijelosti brojiti kao neovisan holdout bez oznaka za pojedinačne ponude.**

- W43: **25/25** Tjek slika 1400×2080; W41: **29/29** Priser24 slika 700×1040.
- Byte-identični parovi stranica: **{exact}** (različite rezolucije i kompresija; nije test semantičke jednakosti).
- Stranica W43 s vizualno uočljivim ponavljanjem kampanje ili proizvoda: **{flag_count}/25** (`{', '.join(f'P{i:02d}' for i in sorted(VISUAL_REUSE))}`). Preostalih **{25-flag_count}** nisu dokazano novi oglasi; traže provjeru na razini ponude.
- Prethodno potvrđeni Tjek katalog: `4S2S5aaF` / `faa0Ym`, `Uke 43`, **25** stranica, `offer_count=98` (broj iz API-ja, nije neovisni ručni broj oglasa).
- Stvarna točnost **proizvod + tiskana cijena** na svim W43 oglasima: **NOT_MEASURED**. Tjekov broj `98` nije ground truth za ukupnu vizualnu pokrivenost.

## Primjeri preklapanja

'''
    for i, (j,note) in VISUAL_REUSE.items():
        audit+=f'- W43 P{i:02d} ↔ W41 P{j:02d}: {note}\n'
    audit+='''
## Metoda i ograničenja

Provjereni su svi parovi stranica (25 × 29). Za svaki par izračunani su SHA-256, MAE na normaliziranim sivim rasterima 64×96 i 224×332, korelacija rastera te ORB značajke s RANSAC homografijom (350×520). ORB može reagirati na ponovljeni branding, pozadinu i raspored, stoga nije dokaz jednakog proizvoda/cijene. Vizualno su pregledani pregledni listovi i relevantni parovi ponovljenih motiva. `page-pairs.csv` bilježi i automatske kandidate i odabrane parove za pregled.

Različiti izvori/rasterizacije onemogućuju usporedbu samo prema hashovima i visokopouzdano proglašavanje novih ponuda. Pojedinačni proizvod može se ponoviti na različitoj stranici ili unutar djelomično izmijenjenog oglasa. To se ne rješava samo usporedbom cijelih stranica.

## Preporučeni sljedeći koraci

1. Sačuvati zamrznuti V5 netaknut. W43 koristiti kao **djelomični neovisni evaluacijski skup**, ne automatski kao potpuno čist holdout.
2. Izraditi kompletnu ručnu referencu W43 (svi oglasi na svih 25 stranica, uključujući propuštene, uvjetne cijene i ne-oglase).
3. Označiti ponovljene **pojedinačne ponude** s W41, pa zasebno računati rezultat *novih ponuda* i ukupni rezultat, uz jasno naveden denominator.
4. Tek zatim pokrenuti offline image-to-candidates pipeline i zamrznuti V5 dio za nazive, bez Tjek matching-a kao ground trutha. Točan W41 V5 nije samostalan image-to-offers parser.

## Datoteke

`review-overlap.html` je interaktivni offline pregled svih 25 W43 stranica nasuprot odabranom W41 paru; klik na sliku otvara veći prikaz. `page-pairs.csv` i `summary.json` sadrže reproducibilne metrike; `w43_overlap_audit.py` ponovno računa rezultate iz dva izvorna ZIP-a, bez API-ja/OCR-a/upisa u bazu.
'''
    (out/'audit.md').write_text(audit,encoding='utf-8')
    return summary


def create_html(rows:list[dict],out:Path)->None:
    heads='''<!doctype html><html lang="hr"><head><meta charset="utf-8"><title>REMA W43 vs W41 — vizualni audit</title><style>
body{font:15px system-ui,-apple-system,sans-serif;line-height:1.45;color:#1b2530;max-width:1300px;margin:35px auto;padding:0 20px;background:#f5f7fa}
h1{margin:0 0 6px} .sub{color:#596679}.warning{background:white;border-left:4px solid #c78b13;padding:15px;margin:14px 0 22px}
.controls{position:sticky;top:0;background:#f5f7fa;padding:12px 0;z-index:2}select{padding:8px;font-size:15px}
.card{background:#fff;border:1px solid #dce2e9;border-radius:12px;margin:12px 0;padding:16px}h2{margin:0;font-size:19px}
.row{display:flex;gap:20px;flex-wrap:wrap}.half{min-width:180px;flex:1}.half img{width:100%;max-width:325px;max-height:490px;object-fit:contain;border:1px solid #ddd}.half small{display:block;color:#667282}
.meta{font-size:13px;color:#576675}.flag{font-weight:650;color:#974b0e}.neutral{color:#536a70}a{color:#175794}
</style></head><body><h1>REMA W43 ↔ W41 — preflight</h1>
<p class="sub">25 stranica W43 (Tjek, 1400×2080) ↔ 29 stranica W41 (Priser24, 700×1040). Bez OCR-a i API poziva.</p>
<div class="warning"><b>Ocjena: djelomično preklapanje.</b> Ovdje su označene ponovljene kampanje ili ponude samo prema vizualnom pregledu. Nijedna stranica nije automatski proglašena potpuno neovisnom, niti je izračunana točnost parsera. Ponavljanje pojedinačnih oglasa može postojati i na neoznačenim stranicama.</div>
<div class="controls"><label>Prikaz: <select id="filter"><option value="all">Sve stranice (25)</option><option value="repeat">Vidljivo ponavljanje</option><option value="other">Ostalo / zahtijeva provjeru</option></select></label></div>'''
    out_text=[heads]
    for r in rows:
        i,j =r['w43_page'],r['w41_review_page']
        flagged=r['preview_flag'].startswith('VISIBLE')
        status='Vidljivo ponavljanje kampanje/oglasa' if flagged else 'Nije potvrđeno ponavljanje pojedine ponude — ručni pregled nužan'
        tag='repeat' if flagged else 'other'
        note=html.escape(r['review_note'])
        out_text.append(f'''<section class="card" data-kind="{tag}"><h2>W43 str. {i:02d} ↔ W41 str. {j:02d}</h2>
<p class="{'flag' if flagged else 'neutral'}">{status}</p><p>{note}</p>
<div class="row"><div class="half"><small>W43 P{i:02d}</small><a target="_blank" href="previews/w43-{i:02d}.jpg"><img loading="lazy" src="previews/w43-{i:02d}.jpg" alt="W43 str. {i}"></a></div>
<div class="half"><small>W41 P{j:02d}</small><a target="_blank" href="previews/w41-{j:02d}.jpg"><img loading="lazy" src="previews/w41-{j:02d}.jpg" alt="W41 str. {j}"></a></div></div>
<p class="meta">ORB inlier: {r['orb_inliers']} / good: {r['orb_good_matches']} | MAE 64×96: {r['gray_mae_64']} | korelacija: {r['gray_correlation_64']}<br>Automatski najbolji po MAE: W41 P{r['w41_best_pixel']:02d}; po ORB-u: W41 P{r['w41_best_feature']:02d}. Ove metrike ne identificiraju ponude.</p></section>''')
    out_text.append('''<script>document.getElementById('filter').addEventListener('change',e=>{for(const el of document.querySelectorAll('.card'))el.hidden=(e.target.value!=='all'&&el.dataset.kind!==e.target.value);});</script></body></html>''')
    (out/'review-overlap.html').write_text('\n'.join(out_text),encoding='utf-8')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--w43',type=Path,required=True)
    ap.add_argument('--w41',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    summary=generate(args.w43,args.w41,args.output)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
