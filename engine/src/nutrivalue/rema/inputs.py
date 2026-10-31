"""Strict local input validation; catalog identity is data, never parser logic."""
from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path


class ValidationError(ValueError):
    """Invalid or insufficiently proven inputs; no completed output is published."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_path(path: Path) -> Path:
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValidationError(f'Symlink is forbidden: {part}')
    if not path.is_file():
        raise ValidationError(f'Missing regular file: {path}')
    return path


def local_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValidationError('File references must be local relative paths without traversal')
    path = regular_path(root / name)
    if not path.is_relative_to(root.absolute()):
        raise ValidationError('File outside its declared directory')
    return path


def read_json(path: Path) -> dict:
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValidationError(f'Duplicate JSON field: {key}')
            result[key] = value
        return result
    try:
        result = json.loads(regular_path(path).read_text(encoding='utf-8'), object_pairs_hook=unique,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValidationError(value)))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f'Invalid JSON: {path}: {exc}') from exc
    if not isinstance(result, dict):
        raise ValidationError(f'Expected JSON object: {path}')
    return result


def check_hash(data: bytes, expected: str, label: str) -> str:
    actual = sha256(data)
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected) or actual != expected:
        raise ValidationError(f'SHA-256 mismatch: {label}')
    return actual


def positive_int(value, label):
    if type(value) is not int or value <= 0:
        raise ValidationError(f'{label} must be a positive integer')
    return value


def image_info(raw: bytes) -> tuple[int, int, str]:
    """Read dimensions by content, independent of filename extension.

    OCR still decodes the raster itself. PNG/JPEG/WebP are deliberately bounded
    supported input encodings; an unknown/corrupt header is never guessed.
    """
    try:
        if raw.startswith(b'\x89PNG\r\n\x1a\n') and raw[12:16] == b'IHDR':
            if len(raw) < 45 or raw[-8:-4] != b'IEND':
                raise ValidationError('Truncated PNG')
            width, height = struct.unpack('>II', raw[16:24])
            mime = 'image/png'
        elif raw.startswith(b'\xff\xd8'):
            if not raw.endswith(b'\xff\xd9'):
                raise ValidationError('Truncated JPEG')
            pos = 2
            while pos < len(raw):
                if raw[pos] != 255:
                    raise ValidationError('Invalid JPEG marker')
                while raw[pos] == 255:
                    pos += 1
                marker = raw[pos]; pos += 1
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    continue
                size = int.from_bytes(raw[pos:pos + 2], 'big')
                if size < 2 or pos + size > len(raw):
                    raise ValidationError('Invalid JPEG segment')
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    height, width = struct.unpack('>HH', raw[pos + 3:pos + 7])
                    break
                pos += size
            else:
                raise ValidationError('JPEG dimensions missing')
            mime = 'image/jpeg'
        elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
            if len(raw) != int.from_bytes(raw[4:8], 'little') + 8:
                raise ValidationError('Truncated WebP')
            kind = raw[12:16]
            if kind == b'VP8X':
                width = 1 + int.from_bytes(raw[24:27], 'little')
                height = 1 + int.from_bytes(raw[27:30], 'little')
            elif kind == b'VP8 ' and raw[23:26] == b'\x9d\x01\x2a':
                width, height = struct.unpack('<HH', raw[26:30])
                width &= 0x3FFF; height &= 0x3FFF
            elif kind == b'VP8L' and raw[20] == 0x2F:
                bits = int.from_bytes(raw[21:25], 'little')
                width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            else:
                raise ValidationError('Unsupported WebP encoding')
            mime = 'image/webp'
        else:
            raise ValidationError('Unsupported raster content (use PNG/JPEG/WebP)')
        positive_int(width, 'image width'); positive_int(height, 'image height')
        return width, height, mime
    except (IndexError, struct.error) as exc:
        raise ValidationError('Corrupt image header') from exc


def indexed_pages(rows, count: int, label: str) -> dict:
    if not isinstance(rows, list):
        raise ValidationError(f'{label}: pages must be a list')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError(f'{label}: invalid page entry')
        page = positive_int(row.get('page'), 'page')
        if page in result:
            raise ValidationError(f'{label}: duplicate page {page}')
        result[page] = row
    if set(result) != set(range(1, count + 1)):
        raise ValidationError(f'{label}: missing or extra pages; expected every page 1..{count}')
    return result


def load_catalog(metadata: Path, manifest: Path, image_dir: Path | None = None) -> dict:
    metadata, manifest = Path(metadata), Path(manifest)
    source, images = read_json(metadata), read_json(manifest)
    if source.get('schema') not in (None, 'nutrivalue_rema_catalog_v1'):
        raise ValidationError('Unsupported catalog schema')
    if images.get('schema') not in (None, 'nutrivalue_rema_images_v1'):
        raise ValidationError('Unsupported image manifest schema')
    identity = source.get('id', source.get('catalog_id'))
    if not isinstance(identity, str) or not identity.strip() or identity != images.get('catalog_id'):
        raise ValidationError('Catalog identity mismatch or missing identity')
    dealer = source.get('dealer', {})
    if not isinstance(dealer, dict):
        raise ValidationError('Invalid dealer')
    branding = source.get('branding', {})
    if not isinstance(branding, dict):
        raise ValidationError('Invalid catalog branding')
    names = [dealer.get('name'), branding.get('name')]
    names = [re.sub(r'\s+', '', str(n)).upper() for n in names if n]
    if not names or any(n != 'REMA1000' for n in names):
        raise ValidationError('Catalog dealer must be REMA 1000')
    dealer_id = source.get('dealer_id', dealer.get('id'))
    if dealer.get('id') and dealer_id != dealer['id']:
        raise ValidationError('Inconsistent catalog dealer IDs')
    if images.get('dealer_id') is not None and images['dealer_id'] != dealer_id:
        raise ValidationError('Image manifest dealer mismatch')
    week = images.get('week', source.get('week'))
    if source.get('week') is not None and source['week'] != week:
        raise ValidationError('Week metadata mismatch')
    count = positive_int(source.get('page_count'), 'page_count')
    if type(images.get('page_count', count)) is not int or images.get('page_count', count) != count:
        raise ValidationError('Manifest page count mismatch')
    rows = indexed_pages(images.get('pages'), count, 'images')
    root = Path(image_dir) if image_dir is not None else manifest.parent
    pages = []
    for number, row in sorted(rows.items()):
        path = local_file(root, row.get('file'))
        raw = path.read_bytes()
        checksum = check_hash(raw, row.get('sha256'), str(path))
        if 'bytes' in row and (type(row['bytes']) is not int or row['bytes'] != len(raw)):
            raise ValidationError(f'Image byte count mismatch: {path}')
        width, height, mime = image_info(raw)
        pages.append({'page': number, 'path': path, 'sha256': checksum, 'width': width,
                      'height': height, 'mime': mime})
    # Do not propagate signed URLs or provider offer values to review output.
    return {'catalog_id': identity, 'dealer_id': dealer_id, 'week': week, 'page_count': count,
            'valid_from': source.get('run_from'), 'valid_till': source.get('run_till'),
            'label': source.get('label'), 'source_metadata_sha256': sha256(metadata.read_bytes()),
            'source_manifest_sha256': sha256(manifest.read_bytes()), 'pages': pages}
