#!/usr/bin/env python3
"""Process a local REMA catalog into private review artifacts, entirely offline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from nutrivalue.rema.inputs import ValidationError
from nutrivalue.rema.pipeline import process_catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True, help='Local catalog metadata JSON')
    parser.add_argument('--manifest', type=Path, required=True, help='Local image manifest JSON')
    parser.add_argument('--images', type=Path, help='Image directory; default: manifest directory')
    parser.add_argument('--ocr-cache', type=Path, help='Existing hash-bound ocr-manifest.json directory')
    parser.add_argument('--output', type=Path, required=True, help='New directory below data/private/tjek/')
    parser.add_argument('--language', default='nor+eng', help='Installed local Tesseract languages')
    parser.add_argument('--psm', type=int, default=11, help='Local Tesseract segmentation mode')
    parser.add_argument('--timeout', type=float, default=120, help='Maximum local OCR seconds per page (1..600)')
    args = parser.parse_args(argv)
    try:
        report = process_catalog(args.catalog, args.manifest, args.output, image_dir=args.images,
                                 ocr_cache=args.ocr_cache, language=args.language, psm=args.psm, timeout=args.timeout)
    except (ValidationError, OSError) as exc:
        print(f'REMA PROCESSING: FAILED — {exc}', file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
