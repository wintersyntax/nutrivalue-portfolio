#!/usr/bin/env python3
"""NON-INDEPENDENT reference comparison. NEVER an inference component."""
from pathlib import Path
import argparse,collections,csv,json,re,html,shutil
from difflib import SequenceMatcher
from w41_title_refiner_v5 import norm

def evaluate(v5_path,reference_csv,out_dir,images=None):
    data=json.loads(Path(v5_path).read_text(encoding='utf-8'))
    refs={r['candidate_id']:r for r in csv.DictReader(Path(reference_csv).open(encoding='utf-8-sig'))}
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    results=[]
    for c in data['candidates']:
        id=c['candidate_id'];r=refs.get(id)
        if r is None:raise ValueError('AI preliminary reference missing '+id)
        title=c['proposed_product_title_v5'];old=c.get('guarded_product_title_v3') or ''
        expect=r['expected_name_preliminary'];expect_n=norm(re.sub(r'\([^)]*\)','',expect))
        new_n=norm(title)
        orig_good=r['title_strict_match_preliminary'].strip().lower()=='true'
        if c['pricing_mode'] in ('COMPOSITE_TOTAL','GRAPHIC_NOT_OFFER'):
            verdict='NOT_AN_ITEM';title_good=False
        elif title==old:
            verdict='V4_AI_VERDICT_UNCHANGED';title_good=orig_good
        elif new_n==expect_n:
            verdict='MATCHES_PRIOR_AI_NAME_NORMALIZED';title_good=True
        elif orig_good and norm(old) in new_n:
            verdict='SAME_AI_PRODUCT_EXTENDED_WITH_ADDITIONAL_OCR';title_good=True
        elif expect_n in new_n:
            verdict='COMPATIBLE_WITH_AI_REFERENCE_MORE_SPECIFIC';title_good=True
        elif new_n and len(new_n)>7 and SequenceMatcher(None,new_n,expect_n).ratio()>=.90:
            verdict='HIGH_TEXT_SIMILARITY_NEEDS_VISUAL_SIGNOFF';title_good=False
        else:
            verdict='NOT_MATCHED_TO_PRIOR_AI_REFERENCE';title_good=False
        pm=r['printed_field_matches_preliminary'].lower()=='true'
        pi=r['paddle_inclusive_price_evidence_match_preliminary'].lower()=='true'
        # Supplemental cached unit-rate for MELON is REVIEW ONLY. It must not be
        # counted as accepted or independently verified current price.
        rows=dict(candidate_id=id,page=c['page'],old_title=old,v5_title=title,expected_ai_preliminary=expect,changed=c['title_changed_v5'],title_evaluation_v5=verdict,strict_ai_reference_alignment=title_good,main_price_current_nok=c.get('printed_current_nok') or '',paddle_review_only_nok=c.get('targeted_paddle_price_candidate_nok_v4') or '',unit_price_conflict_alternative_nok=c.get('price_alternative_nok_v5') or '',main_price_matches_ai_reference=pm,paddle_inclusive_evidence_matches_ai_reference=pi,joint_main_and_title_ai_alignment=bool(title_good and pm),joint_including_unaccepted_paddle_ai_alignment=bool(title_good and pi),independent_human_review='PENDING')
        results.append(rows)
    valid=[z for z in results if z['candidate_id'] in refs and z['title_evaluation_v5']!='NOT_AN_ITEM']
    scores={
        'benchmark_status':'AI_PRELIMINARY_REFERENCE_NOT_INDEPENDENT',
        'official_full_catalog_recall':'NOT_MEASURED_REVIEW_INCOMPLETE',
        'source':'V4 preliminary AI visual assessment, corrected page17 labels; not new human review',
        'pages':29,'candidates':len(results),'preliminary_offers':len(valid),
        'aligned_product_titles_to_preliminary_AI_reference':sum(z['strict_ai_reference_alignment'] for z in valid),
        'joint_main_price_and_title_to_preliminary_AI_reference':sum(z['joint_main_and_title_ai_alignment'] for z in valid),
        'joint_including_UNACCEPTED_paddle_and_title_to_preliminary_AI_reference':sum(z['joint_including_unaccepted_paddle_ai_alignment'] for z in valid),
        'price_main_AI_preliminary_matches':sum(z['main_price_matches_ai_reference'] for z in valid),
        'price_including_unaccepted_Paddle_AI_preliminary_matches':sum(z['paddle_inclusive_evidence_matches_ai_reference'] for z in valid),
        'title_comparison_categories':dict(collections.Counter(z['title_evaluation_v5'] for z in valid)),
        'review_signoffs':0,'verified_tjek_skus':0,
        'disallowed_as_production_quality_metric':True
    }
    with (out/'v5-preliminary-comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
    (out/'v5-preliminary-scoring.json').write_text(json.dumps(scores,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    md=['# W41 V5 — preliminarna usporedba (NIJE neovisni benchmark)','','Reference su rezultat ranijeg AI-vizualnog audita, a ne neovisne ljudske provjere.','',f"- **{len(results)}** V5 kandidata, od kojih je **{len(valid)}** preliminarnih ponuda.\n- Naziv usklađen s prethodnom AI referencom: **{scores['aligned_product_titles_to_preliminary_AI_reference']}/{len(valid)}**.\n- Naziv + glavna cijena usklađeni s AI referencom: **{scores['joint_main_price_and_title_to_preliminary_AI_reference']}/{len(valid)}**.\n- Naziv + cijena uključujući dva NEPRIHVAĆENA Paddle prijedloga: **{scores['joint_including_UNACCEPTED_paddle_and_title_to_preliminary_AI_reference']}/{len(valid)}**.\n- Izvorni OCR konflikt dinje ostaje neodobren; alternativna cijena se NE računa u točnu glavnu cijenu.", '', '## Granice provjere', '', 'Ova usporedba nije mjerenje stvarnog recall-a. Prethodne AI reference i nazivnik 100 nisu verificirani od neovisnog ljudskog recenzenta. Neke semantički razumljive zamjene mogu ostati neusklađene s doslovnom referencom. Za službenu stopu treba pregledati sve 29 stranica, sve stvarne oglase i sve promjene naziva.','','Sva polja `independent_human_review` su `PENDING`. Nema prihvaćenih cijena, potvrđenih SKU-ova ni upisa u bazu.']
    (out/'COMPARISON-v5.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    if images:
        source=Path(images)
        if not source.is_dir(): raise FileNotFoundError(f'Missing original flyer images: {source}')
        for page in range(1,30):
            if not (source/f'page-{page:02d}.jpg').is_file():raise FileNotFoundError(f'Original page {page} missing in {source}')
        (out/'images').mkdir(exist_ok=True)
        for page in range(1,30):
            shutil.copy2(source/f'page-{page:02d}.jpg',out/'images'/f'page-{page:02d}.jpg')
        chunks=['<!doctype html><html lang="hr"><meta charset="utf-8"><title>W41 V5 pregled</title>','<style>body{font-family:system-ui;background:#f7f9fc;color:#243044;max-width:1450px;margin:25px auto;padding:0 24px}table{border-collapse:collapse;font-size:12px;width:100%;background:white}td,th{border:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}th{background:#eaf0f8}section{margin:22px 0 45px}.layout{display:grid;grid-template-columns:340px auto;gap:20px}img{width:330px}strong{color:#273f68}.bad{background:#fff2e9}p{line-height:1.55}@media(max-width:700px){.layout{display:block;overflow-x:auto}}</style>', '<h1>REMA 1000 W41 — V5 preliminarni pregled</h1>', '<p><strong>NIJE neovisni benchmark.</strong> Svi kandidati i usporedbe imaju status REVIEW ONLY; AI referenca možda sadrži preostale greške. Prikazane slike služe ljudskoj provjeri.</p>']
        for page in range(1,30):
            rows=[z for z in results if z['page']==page]
            chunks.append(f'<section><h2>Stranica {page:02d}</h2><div class="layout">')
            pic=source/f'page-{page:02d}.jpg'
            chunks.append(f'<a href="images/page-{page:02d}.jpg"><img loading="lazy" src="images/page-{page:02d}.jpg" alt="Stranica {page}" /></a>' if pic.exists() else '<div>Originalna slika nije dostavljena.</div>')
            chunks.append('<div style="overflow-x:auto"><table><tr><th>ID</th><th>V4</th><th>V5</th><th>AI preliminarna referenca</th><th>Cijena</th><th>Rezultat</th></tr>')
            for z in rows:
                fields=[z['candidate_id'],z['old_title'],z['v5_title'],z['expected_ai_preliminary'],z['main_price_current_nok'] or ('Paddle review '+z['paddle_review_only_nok'] if z['paddle_review_only_nok'] else '—'),z['title_evaluation_v5']]
                chunks.append('<tr '+('class="bad"' if not z['strict_ai_reference_alignment'] else '')+'>' + ''.join('<td>'+html.escape(str(x))+'</td>' for x in fields)+'</tr>')
            chunks.append('</table></div></div></section>')
        chunks.append('</html>')
        (out/'review-v5-preliminary.html').write_text(''.join(chunks),encoding='utf-8')
    print(json.dumps(scores,ensure_ascii=False,indent=2));return scores

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--v5',required=True);ap.add_argument('--reference-v4-ai-csv',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--images');a=ap.parse_args();evaluate(a.v5,a.reference_v4_ai_csv,a.out_dir,a.images)
