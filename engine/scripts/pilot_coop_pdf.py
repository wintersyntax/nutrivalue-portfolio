#!/usr/bin/env python3
"""Extract 10 source-only candidates from the hash-pinned private Coop PDF.

Example (after installing PyMuPDF in a private venv):
  python scripts/pilot_coop_pdf.py --pdf data/private/coop-flyers/felles-42.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from nutrivalue.providers.coop_pdf_pilot import (  # noqa: E402
    CoopPdfPilotError, save_private,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, required=True, help='Saved local PDF below data/private')
    parser.add_argument('--out', type=Path, default=ROOT / 'data/private/coop-pdf-pilot/cards-review.json')
    args = parser.parse_args(argv)
    try:
        report = save_private(args.pdf, args.out, ROOT / 'data/private')
    except (CoopPdfPilotError, OSError, ValueError) as exc:
        print(f'Coop PDF pilot: RED - {exc}', file=sys.stderr)
        return 1
    print(f"Coop PDF pilot: {len(report['cards'])} source-only candidates from 20-page private PDF")
    for c in report['cards']:
        print(f"  p{c['source_page']:02} {c['source_title']}: "
              f"{c['display_price_ore'] / 100:.2f} NOK ({c['price_basis']}, {c['offer_kind']}); "
              f"pack candidate={c['package_grams_candidate']} g")
    print('Validity/store/rights/EAN/nutrition unverified; 0 trusted protein-value rows.')
    print(f'PRIVATE REVIEW: {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
