#!/usr/bin/env python3
"""One read-only request to an explicitly pinned public grocery page.

Results are analysis evidence only, never a price import. Output stays in the
ignored private data directory. No Firecrawl, cookies or authentication.
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.source_probe import (  # noqa: E402
    SOURCES, SourceProbeError, analyze_html, fetch_spaced, save_private_snapshot,
)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(SOURCES), required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fetch", action="store_true", help="One public HTTP GET, no retries")
    mode.add_argument("--input-html", type=Path, help="Replay previously saved public HTML")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data/private/source-probes")
    args = parser.parse_args(argv)
    try:
        if args.fetch:
            payload, final_url = fetch_spaced(args.source, args.out)
        else:
            payload, final_url = args.input_html.read_bytes(), None
        report = analyze_html(args.source, payload, final_url=final_url,
                              retrieval="public_http" if args.fetch else "saved_html")
        raw, info = save_private_snapshot(payload, report, args.out)
    except (OSError, UnicodeError, SourceProbeError, urllib.error.URLError) as exc:
        print(f"SOURCE PROBE: RED - {exc}", file=sys.stderr)
        return 1
    print(f"SOURCE PROBE: RECORDED {args.source} ({report['retrieval']})")
    print(f"Evidence: {report['evidence_state']}; markers: "
          f"{len(report['marker_phrases_found'])}/{len(report['marker_phrases_expected'])}; "
          f"H3: {report['heading_counts']['h3']}; "
          f"currency mentions: {report['currency_text_mentions']}")
    if args.source in ("coop_prix_weekly", "coop_extra_weekly"):
        print("Coop readiness: "
              f"articles={report['coop_article_count']}; "
              f"placeholder article H3={report['coop_placeholder_article_h3_count']}; "
              f"named article H3={len(report['coop_non_placeholder_h3_sample'])}; "
              f"render review required={report['coop_requires_render_review']}")
    if args.source in ("meny_campaigns", "meny_weekly"):
        print("MENY readiness: "
              f"article H3={report['meny_article_h3_count']}; "
              f"loaders={report['meny_loader_phrases_found']}; "
              f"postcode prompt={report['meny_postcode_prompt_visible']}; "
              f"render review required={report['meny_requires_render_review']}; "
              "store-specific price verified=False")
    print(f"PRIVATE HTML: {raw}")
    print(f"PRIVATE REPORT: {info}")
    print("No price, EAN, offer validity, actual store or protein value verified.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
