#!/usr/bin/env python3
"""Offline source-only MENY Oatly PDF + first-party SKU-page price join.

Prerequisites: one public HTML snapshot *per selected SKU* using:
  python scripts/probe_public_sources.py --source meny_oatly_barista_500 --fetch
Do not circumvent the shared MENY host pacing or retry HTTP 429. No network here.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from nutrivalue.providers.meny_oatly_link import (  # noqa: E402
    OatlyLinkError, TARGET_INDEX, join_oatly,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf-report', type=Path,
                        default=ROOT / 'data/private/meny-pdf-pilot/cards-review.json')
    parser.add_argument('--source-root', type=Path,
                        default=ROOT / 'data/private/source-probes')
    parser.add_argument('--out', type=Path,
                        default=ROOT / 'data/private/meny-oatly-link/price-candidates.json')
    parser.add_argument('--source', action='append', choices=sorted(TARGET_INDEX),
                        required=True, help='One to four uniquely pinned product pages')
    parser.add_argument('--render-report', type=Path, default=None,
                        help='Previously saved Chromium Barista report.json '
                             '(no browser/network; requires only Barista source)')
    args = parser.parse_args(argv)
    try:
        joined = join_oatly(args.pdf_report, args.source_root, args.out,
                            ROOT / 'data/private', sources=args.source,
                            render_report=args.render_report)
    except (OatlyLinkError, OSError, ValueError) as exc:
        print(f'MENY OATLY JOIN: RED - {exc}', file=sys.stderr)
        return 1
    print(f"MENY OATLY JOIN: {len(joined['eligible_online_product_price_candidates'])} "
          'source-only priced SKU candidates; no local store/checkout verification')
    for item in joined['eligible_online_product_price_candidates']:
        print(f"  {item['product_label_candidate']}: "
              f"{item['base_price_ore_source_only'] / 100:.2f} NOK "
              f"({item['source']}, {item['captured_at']})")
    print('Flyer Oatly base price remains NULL; 33% checkout discount is '
          'conditional, not a confirmed basket price.')
    print(f"PRIVATE JOIN: {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
