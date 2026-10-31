#!/usr/bin/env python3
"""Offline replay of the owner's exact MENY week-42 Monday PDF, no network.

Example:
  data/private/.venv-render/bin/python scripts/pilot_meny_pdf.py \
    --pdf data/private/meny-flyers/Uke_42_mandag.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from nutrivalue.providers.meny_pdf_pilot import (  # noqa: E402
    MenyPdfPilotError, save_private,
)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--pdf', type=Path, required=True,
                     help='Exact manually downloaded PDF under data/private')
    cli.add_argument('--out', type=Path,
                     default=ROOT / 'data/private/meny-pdf-pilot/cards-review.json')
    args = cli.parse_args(argv)
    try:
        data = save_private(args.pdf, args.out, ROOT / 'data/private')
    except (MenyPdfPilotError, ValueError, OSError) as exc:
        print(f'MENY PDF pilot: RED - {exc}', file=sys.stderr)
        return 1
    print(f"MENY PDF pilot: {len(data['cards'])} source-only candidates "
          f"from {data['page_count']} page private PDF")
    for card in data['cards']:
        price = ('unknown (3-for-2, no base price)' if
                 card['display_price_ore'] is None else
                 f"{card['display_price_ore'] / 100:.2f} NOK")
        print(f"  p{card['source_page']:02} {card['source_name']}: {price}"
              f" ({card['price_basis']}, {card['offer_kind']}); "
              f"package={card['package_quantity_candidate']} "
              f"{card['package_unit_candidate']}; count={card['pack_count_candidate']}")
    print('Offer/store/rights/EAN/nutrition not independently verified; '
          '0 trusted protein-value rows.')
    print(f'PRIVATE REVIEW: {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
