"""Private, self-contained visual review exports, with complete page diagnostics."""
from __future__ import annotations

import base64
import csv
import html
import json
from pathlib import Path

from .inputs import check_hash


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def safe_csv(value):
    text = str(value) if value is not None else ''
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def overlay(image: dict, result: dict) -> str:
    raw = image['path'].read_bytes()
    check_hash(raw, image['sha256'], 'image before overlay export')
    encoded = base64.b64encode(raw).decode('ascii')
    width, height = image['width'], image['height']
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             f'<title>Page {image["page"]}: review-only advert hypotheses</title>',
             f'<image width="{width}" height="{height}" href="data:{image["mime"]};base64,{encoded}"/>']

    def rectangle(box, color, label='', dashed=False):
        x, y = box['x'] * width, (1 - box['y'] - box['height']) * height
        dash = 'stroke-dasharray="8 5"' if dashed else ''
        parts.append(f'<rect x="{x:.3f}" y="{y:.3f}" width="{box["width"] * width:.3f}" '
                     f'height="{box["height"] * height:.3f}" fill="none" stroke="{color}" '
                     f'stroke-width="2" {dash}><title>{html.escape(label)}</title></rect>')
        if label:
            parts.append(f'<text x="{x:.3f}" y="{max(14, y - 4):.3f}" font-size="14" '
                         f'fill="{color}" stroke="white" stroke-width=".4">{html.escape(label)}</text>')
    for line in result['unassigned_lines']:
        rectangle(line['box'], '#777', dashed=True)
    for index, advert in enumerate(result['adverts'], 1):
        rectangle(advert['region'], '#b45309', f'{index}: {advert["product_name"] or "NAME MISSING"}', True)
        for signal in advert['evidence']:
            rectangle(signal['box'], {'current_price': '#0e7490', 'former_price': '#be123c',
                                      'unit_price': '#7e22ce'}.get(signal['kind'], '#4338ca'))
    parts.append('</svg>')
    return '\n'.join(parts)


def export_review(root: Path, catalog: dict, pages: list, results: list, ocr: dict, report: dict):
    (root / 'pages').mkdir(); (root / 'adverts').mkdir(); (root / 'ocr').mkdir()
    provenance = {k: v for k, v in catalog.items() if k != 'pages'}
    adverts = [a for r in results for a in r['adverts']]
    write_json(root / 'adverts.json', {'schema': 'nutrivalue_rema_adverts_v1', 'provenance': provenance,
                                      'verification_status': 'review_only', 'adverts': adverts})
    write_json(root / 'coverage.json', {'schema': 'nutrivalue_rema_coverage_v1', 'pages': results})
    write_json(root / 'report.json', report)
    write_json(root / 'ocr/ocr-lines.json', {'schema': 'nutrivalue_rema_ocr_v1',
               'coordinate_system': 'normalized_bottom_left', **provenance,
               'engine': ocr['engine'], 'language': ocr['language'], 'psm': ocr['psm'], 'pages': pages})
    manifest_pages = []
    for page in pages:
        number = page['page']; name = f'page-{number:04d}.tsv'
        (root / 'ocr' / name).write_bytes(ocr['tsv'][number])
        manifest_pages.append({'page': number, 'image_sha256': page['image_sha256'], 'tsv_file': name,
                               'tsv_sha256': page['tsv_sha256'], 'recognized_lines': len(page['lines'])})
    write_json(root / 'ocr/ocr-manifest.json', {'schema': 'nutrivalue_rema_ocr_manifest_v1',
               'catalog_id': catalog['catalog_id'], 'week': catalog['week'],
               'source_manifest_sha256': catalog['source_manifest_sha256'],
               'page_count': catalog['page_count'], 'total_recognized_lines': report['ocr_lines'],
               'tesseract_version': ocr['engine'], 'language': ocr['language'], 'psm': ocr['psm'],
               'pages': manifest_pages, 'source_cache_manifest_sha256': ocr.get('cache_manifest_sha256'),
               'normalization_warnings': ocr['normalization_warnings'] + ocr.get('upstream_normalization_warnings', [])})
    for a in adverts:
        write_json(root / 'adverts' / f'{a["id"]}.json', a)
    fields = ['id', 'catalog_id', 'week', 'page', 'product_name', 'current_price_nok', 'former_prices_nok',
              'unit_prices', 'percentage_discounts', 'multibuy', 'conditions', 'pricing_mode',
              'issues', 'verification_status', 'human_approved']
    with (root / 'review.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for a in adverts:
            writer.writerow({k: safe_csv(json.dumps(a[k], ensure_ascii=False) if isinstance(a[k], (list, dict))
                                         else a[k]) for k in fields})
    sections = []
    for image, result in zip(catalog['pages'], results):
        number = image['page']; name = f'page-{number:04d}.svg'
        (root / 'pages' / name).write_text(overlay(image, result), encoding='utf-8')
        write_json(root / 'pages' / f'page-{number:04d}.json', result)
        rows = []
        for a in result['adverts']:
            title = html.escape(a['product_name'] or 'MISSING PRODUCT NAME')
            price = html.escape(a['current_price_nok'] or 'Unresolved / conditional')
            conditions = html.escape('; '.join(a['conditions']))
            issues = html.escape('; '.join(a['issues']) or 'Candidate; requires human review')
            details = html.escape(json.dumps({k: a[k] for k in ('former_prices_nok', 'unit_prices',
                                        'percentage_discounts', 'multibuy', 'source_text')}, ensure_ascii=False, indent=2))
            rows.append(f'<tr><td><a href="adverts/{a["id"]}.json">{title}</a></td><td>{price}</td>'
                        f'<td>{conditions}</td><td>{issues}<details><summary>OCR evidence</summary><pre>{details}</pre>'
                        '</details></td></tr>')
        sections.append(f'<section id="page-{number}"><h2>Page {number}</h2>'
                        f'<p>{len(result["adverts"])} advert hypotheses; '
                        f'{len(result["unassigned_lines"])} unassigned OCR lines. '
                        f'<a href="pages/page-{number:04d}.json">Complete diagnostics</a></p>'
                        f'<a href="pages/{name}"><img loading="lazy" src="pages/{name}" '
                        f'alt="Page {number}: advert regions, price evidence and unassigned text"></a>'
                        '<table><thead><tr><th>Printed name candidate</th><th>Current NOK candidate</th>'
                        '<th>Conditions</th><th>Review issues</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></section>')
    nav = ' '.join(f'<a href="#page-{p["page"]}">{p["page"]}</a>' for p in pages)
    title = html.escape(f'REMA 1000 — {catalog["week"] or catalog["label"] or catalog["catalog_id"]}')
    document = f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{title} — private visual review</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:1100px;padding:0 1rem;color:#222}}
img{{width:100%;height:auto;max-height:1000px;object-fit:contain}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}
th,td{{border:1px solid #bbb;padding:.5rem;text-align:left;vertical-align:top}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
a{{color:#075985}}nav{{line-height:2.5}}nav a{{padding:.4rem}}section{{margin-top:3rem}}.notice{{border-left:5px solid #b45309;padding:1rem;background:#fff7ed}}</style>
<h1>{title}</h1><p class="notice"><strong>PRIVATE · REVIEW ONLY</strong> — no prices or product identities are verified.
{report['advert_candidates']} advert hypotheses across {catalog['page_count']} pages. Accuracy and missed adverts: NOT MEASURED.
Repeated promotions remain included. Orange dashed boxes are hypotheses, cyan current-price OCR, red former-price OCR,
purple unit-price OCR, grey unassigned OCR. All regions require visual review.</p>
<p><a href="report.json">Run report</a> · <a href="review.csv">Review CSV</a> · <a href="adverts.json">All advert candidates</a></p>
<nav aria-label="Catalog pages">{nav}</nav>{''.join(sections)}</html>'''
    (root / 'review.html').write_text(document, encoding='utf-8')
