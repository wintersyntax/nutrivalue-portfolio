"""Hash-bound Tesseract cache replay and bounded local OCR to one representation."""
from __future__ import annotations

import csv
import io
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from .inputs import ValidationError, check_hash, indexed_pages, local_file, read_json, sha256

SCHEMA = 'nutrivalue_rema_ocr_v1'
MANIFEST_SCHEMA = 'nutrivalue_rema_ocr_manifest_v1'


def normalize_tsv(raw: str, page: int, width: int, height: int) -> dict:
    """Preserve words, confidence and line IDs in normalized bottom-left boxes."""
    fields = ('level page_num block_num par_num line_num word_num left top width height conf text').split()
    if not raw.endswith('\n'):
        raise ValidationError('Truncated OCR TSV: final newline missing')
    reader = csv.DictReader(io.StringIO(raw), delimiter='\t', quoting=csv.QUOTE_NONE)
    if reader.fieldnames != fields:
        raise ValidationError('Unsupported OCR TSV header')
    groups = defaultdict(list)
    page_headers = 0
    try:
        for row_no, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValidationError(f'Truncated TSV row {row_no}')
            level, source_page = int(row['level']), int(row['page_num'])
            left, top, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
            conf = float(row['conf'])
            if not math.isfinite(conf) or not -1 <= conf <= 100:
                raise ValidationError(f'Invalid TSV confidence row {row_no}')
            if source_page != 1 or not 1 <= level <= 5:
                raise ValidationError('TSV must represent exactly one source image')
            if min(left, top, w, h) < 0 or left + w > width or top + h > height:
                raise ValidationError(f'TSV geometry outside source image: row {row_no}')
            if level == 1:
                page_headers += 1
                if (left, top, w, h) != (0, 0, width, height):
                    raise ValidationError('OCR dimensions do not match validated source image')
            if level != 5 or not row['text'].strip():
                continue
            if w <= 0 or h <= 0 or conf < 0:
                raise ValidationError('Invalid OCR word')
            key = tuple(int(row[k]) for k in ('block_num', 'par_num', 'line_num'))
            box = {'x': left / width, 'y': 1 - (top + h) / height, 'width': w / width, 'height': h / height}
            groups[key].append({'text': row['text'].strip(), 'box': box, 'confidence': round(conf, 5),
                                'source_row': row_no})
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f'Malformed OCR TSV: {exc}') from exc
    if page_headers != 1:
        raise ValidationError('Missing/duplicate TSV page header')
    lines = []
    for key, words in groups.items():
        x = min(w['box']['x'] for w in words); y = min(w['box']['y'] for w in words)
        right = max(w['box']['x'] + w['box']['width'] for w in words)
        top = max(w['box']['y'] + w['box']['height'] for w in words)
        lines.append({'text': ' '.join(w['text'] for w in words),
                      'box': {'x': x, 'y': y, 'width': right - x, 'height': top - y},
                      'confidence': round(sum(w['confidence'] for w in words) / len(words), 5),
                      'source_line': list(key), 'words': words})
    lines.sort(key=lambda l: (-l['box']['y'] - l['box']['height'], l['box']['x'], l['source_line']))
    for index, line in enumerate(lines):
        line['id'] = f'p{page}-l{index}'
    return {'page': page, 'width': width, 'height': height, 'lines': lines}



def quoted_count_evidence(raw: str, expected: int) -> bool:
    """Prove, rather than ignore, a historical CSV-quote row-count defect.

    Tesseract emits literal text, not CSV-quoted fields. Some cache builders
    applied CSV quote folding. Only accept an old count if replaying that
    operation exactly reproduces it AND contains swallowed physical TSV rows.
    Hashes, geometry, physical row completeness and all other checks still apply.
    """
    groups, swallowed = set(), False
    try:
        for row in csv.DictReader(io.StringIO(raw), delimiter='\t'):
            if row.get('level') == '5' and row.get('text', '').strip():
                groups.add(tuple(row[k] for k in ('block_num', 'par_num', 'line_num')))
                if re.search(r'\n[1-5]\t1\t\d+\t', row['text']):
                    swallowed = True
    except (KeyError, TypeError, csv.Error):
        return False
    return swallowed and len(groups) == expected


def read_cache(catalog: dict, cache: Path) -> tuple[list[dict], dict]:
    cache = Path(cache)
    manifest = read_json(cache / 'ocr-manifest.json')
    # Historical caches used a descriptive schema token. Adapt the manifest
    # envelope structurally, then re-read hash-validated TSV, never legacy output.
    if not re.fullmatch(r'nutrivalue_rema_(?:[a-z0-9]+_)?ocr_manifest_v1', str(manifest.get('schema', ''))):
        raise ValidationError('Unsupported OCR manifest schema')
    for field in ('catalog_id', 'source_manifest_sha256', 'page_count'):
        if manifest.get(field) != catalog[field]:
            raise ValidationError(f'OCR cache provenance mismatch: {field}')
    if manifest.get('week') != catalog['week']:
        raise ValidationError('OCR cache week mismatch')
    if not isinstance(manifest.get('tesseract_version'), str) or not manifest['tesseract_version']:
        raise ValidationError('Missing OCR engine provenance')
    if not re.fullmatch(r'[a-z_]+(?:\+[a-z_]+)*', str(manifest.get('language', ''))):
        raise ValidationError('Invalid OCR language provenance')
    if 'psm' not in manifest or (manifest['psm'] is not None and
                                    (type(manifest['psm']) is not int or manifest['psm'] not in range(14))):
        raise ValidationError('Invalid OCR segmentation provenance')
    rows = indexed_pages(manifest.get('pages'), catalog['page_count'], 'OCR cache')
    pages, files, warnings = [], {}, []
    declared_total = 0
    if manifest['psm'] is None or manifest['language'] == 'unrecorded':
        warnings.append({'kind': 'ocr_settings_unrecorded',
                         'note': 'Historical OCR settings unknown; replay validates bytes and image provenance only.'})
    for image in catalog['pages']:
        number = image['page']; row = rows[number]
        if row.get('image_sha256') != image['sha256']:
            raise ValidationError(f'OCR cache image hash mismatch: {number}')
        raw = local_file(cache, row.get('tsv_file')).read_bytes()
        check_hash(raw, row.get('tsv_sha256'), f'OCR page {number}')
        try:
            page = normalize_tsv(raw.decode('utf-8'), number, image['width'], image['height'])
        except UnicodeError as exc:
            raise ValidationError('Invalid TSV encoding') from exc
        declared = row.get('recognized_lines')
        if type(declared) is not int or declared < 0:
            raise ValidationError(f'Invalid cache line count: page {number}')
        if declared != len(page['lines']):
            if not quoted_count_evidence(raw.decode('utf-8'), declared):
                raise ValidationError(f'OCR cache line count mismatch: page {number}')
            warnings.append({'page': number, 'kind': 'quoted_tsv_row_folding',
                             'declared_lines': declared, 'normalized_lines': len(page['lines']),
                             'note': 'Exact quote-folded count reproduced; hashed TSV rows preserved literally.'})
        declared_total += declared
        page['image_sha256'] = image['sha256']; page['tsv_sha256'] = sha256(raw)
        pages.append(page); files[number] = raw
    if (type(manifest.get('total_recognized_lines')) is not int or
            declared_total != manifest['total_recognized_lines']):
        raise ValidationError('OCR cache aggregate line count mismatch')
    return pages, {'mode': 'validated_cache', 'engine': manifest['tesseract_version'],
                   'language': manifest['language'], 'psm': manifest['psm'], 'tsv': files,
                   'declared_lines': declared_total, 'normalization_warnings': warnings,
                   'cache_manifest_sha256': sha256((cache / 'ocr-manifest.json').read_bytes()),
                   'upstream_normalization_warnings': manifest.get('normalization_warnings', [])}


def run_tesseract(catalog: dict, *, language='nor+eng', psm=11, timeout=120) -> tuple[list[dict], dict]:
    if not re.fullmatch(r'[a-z_]+(?:\+[a-z_]+)*', language) or type(psm) is not int or psm not in range(14):
        raise ValidationError('Invalid local OCR settings')
    if not 0 < timeout <= 600:
        raise ValidationError('OCR timeout must be between 0 and 600 seconds per page')
    try:
        version = subprocess.run(['tesseract', '--version'], capture_output=True, text=True,
                                 check=True, timeout=10).stdout.splitlines()[0]
        pages, files = [], {}
        for image in catalog['pages']:
            # Revalidate at point of use. No shell, URLs, automatic installation,
            # cloud credentials or language/model downloads are used.
            check_hash(image['path'].read_bytes(), image['sha256'], 'image before local OCR')
            result = subprocess.run(['tesseract', str(image['path']), 'stdout', '-l', language,
                                     '--psm', str(psm), 'tsv'], capture_output=True, check=True, timeout=timeout)
            page = normalize_tsv(result.stdout.decode('utf-8'), image['page'], image['width'], image['height'])
            page['image_sha256'] = image['sha256']; page['tsv_sha256'] = sha256(result.stdout)
            pages.append(page); files[image['page']] = result.stdout
        return pages, {'mode': 'local_tesseract', 'engine': version, 'language': language, 'psm': psm, 'tsv': files,
                       'declared_lines': sum(len(p['lines']) for p in pages), 'normalization_warnings': []}
    except (OSError, subprocess.SubprocessError, UnicodeError, IndexError) as exc:
        raise ValidationError(f'Local OCR unavailable or failed (no automatic retry): {exc}') from exc
