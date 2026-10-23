"""Atomic offline catalog processing. No database, provider or network integration."""
from __future__ import annotations

import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from .inputs import ValidationError, load_catalog, sha256
from .ocr import read_cache, run_tesseract
from .parser import parse_page
from .review import export_review


def private_output(path: Path) -> Path:
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValidationError('Output traversal is forbidden')
    if not any(path.parts[i:i + 3] == ('data', 'private', 'tjek') for i in range(len(path.parts) - 2)):
        raise ValidationError('Review output must be under data/private/tjek/')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValidationError(f'Output symlink forbidden: {part}')
    if path.exists():
        raise ValidationError(f'Output collision: {path}')
    return path


def process_catalog(metadata: Path, manifest: Path, output: Path, *, image_dir: Path | None = None,
                    ocr_cache: Path | None = None, language='nor+eng', psm=11, timeout=120) -> dict:
    output = private_output(output)
    catalog = load_catalog(metadata, manifest, image_dir)
    # An explicitly supplied cache is authoritative or an error. Never silently
    # rerun OCR after provenance failure or mix partial cached/generated pages.
    pages, ocr = (read_cache(catalog, Path(ocr_cache)) if ocr_cache is not None else
                  run_tesseract(catalog, language=language, psm=psm, timeout=timeout))
    results = [parse_page(page) for page in pages]
    for page, result in zip(pages, results):
        for index, advert in enumerate(result['adverts'], 1):
            identity = sha256((catalog['catalog_id'] + '|' + str(catalog['week']) + '|' +
                               catalog['source_manifest_sha256']).encode())[:16]
            advert.update({'id': f'{identity}-p{page["page"]:04d}-a{index:04d}',
                           'catalog_id': catalog['catalog_id'], 'week': catalog['week'],
                           'valid_from': catalog['valid_from'], 'valid_till': catalog['valid_till'],
                           'image_sha256': page['image_sha256'], 'ocr_tsv_sha256': page['tsv_sha256'],
                           'source_manifest_sha256': catalog['source_manifest_sha256'],
                           'source_metadata_sha256': catalog['source_metadata_sha256']})
    adverts = [a for r in results for a in r['adverts']]
    report = {'schema': 'nutrivalue_rema_report_v1', 'catalog_id': catalog['catalog_id'], 'week': catalog['week'],
              'verification_status': 'review_only', 'source_manifest_sha256': catalog['source_manifest_sha256'],
              'source_metadata_sha256': catalog['source_metadata_sha256'],
              'pages_expected': catalog['page_count'], 'pages_processed': len(pages),
              'images_hash_validated': len(catalog['pages']), 'ocr_mode': ocr['mode'],
              'ocr_engine': ocr['engine'], 'ocr_language': ocr['language'], 'ocr_psm': ocr['psm'],
              'ocr_lines': sum(len(p['lines']) for p in pages), 'advert_candidates': len(adverts),
              'cache_declared_lines': ocr['declared_lines'],
              'normalization_warnings': ocr['normalization_warnings'],
              'upstream_normalization_warnings': ocr.get('upstream_normalization_warnings', []),
              'source_cache_manifest_sha256': ocr.get('cache_manifest_sha256'),
              'implementation_sha256': sha256(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))),
              'provenance_warnings': ([] if catalog['valid_from'] and catalog['valid_till']
                                      else ['catalog_validity_dates_missing']),
              'named_candidates': sum(a['product_name'] is not None for a in adverts),
              'current_price_candidates': sum(a['current_price_nok'] is not None for a in adverts),
              'named_current_price_candidates': sum(a['product_name'] is not None and a['current_price_nok'] is not None
                                                     for a in adverts),
              'conditional_candidates': sum(a['pricing_mode'] != 'ordinary' for a in adverts),
              'missing_current_price': sum('missing_printed_current_price' in a['issues'] for a in adverts),
              'issues': dict(sorted(Counter(i for a in adverts for i in a['issues']).items())),
              'page_coverage': [{'page': p['page'], 'ocr_lines': len(p['lines']),
                                 'advert_candidates': len(r['adverts']),
                                 'unassigned_ocr_lines': len(r['unassigned_lines']),
                                 'status': 'candidate_review_required' if r['adverts'] else 'no_candidates_review_required'}
                                for p, r in zip(pages, results)],
              'accuracy': {'status': 'NOT_MEASURED_REVIEW_INCOMPLETE',
                           'precision': None, 'recall': None, 'false_negatives': None,
                           'independent_human_labels': 0,
                           'note': 'Extraction counts are coverage diagnostics, not verified accuracy. '
                                   'API offer_count is not a ground-truth denominator.'}}
    # Stage in a private sibling directory; invalid inputs never leave a
    # completed review. Exclusive reservation also refuses concurrent collisions.
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / ('.' + output.name + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValidationError(f'Output reserved by another run: {output}') from exc
    os.close(fd)
    stage = None
    try:
        if output.exists():
            raise ValidationError(f'Output collision: {output}')
        stage = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
        export_review(stage, catalog, pages, results, ocr, report)
        if output.exists() or output.is_symlink():
            raise ValidationError(f'Output collision: {output}')
        stage.rename(output)
        stage = None
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        lock.unlink()
    return report
