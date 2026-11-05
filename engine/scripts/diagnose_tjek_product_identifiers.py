#!/usr/bin/env python3
"""TASK-034A: default zero-network identifier diagnostic plan.

Use a private sample manifest for 20–30 categorized offers where available,
OR --html/--expect-sha256 to reuse TASK-033 saved-page IDs (classes unknown).
Without either source, plan only the one owner-reported KIWI example.
--live is mandatory for all HTTP; keys come only from ETILBUDSAVIS_API_KEY.
No raw responses, identifiers, product names, prices or credentials are saved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.providers.tjek_app_cache import AppCacheError  # noqa: E402
from nutrivalue.providers.tjek_product_diagnostic import (  # noqa: E402
    diagnose, html_samples, read_sample_file,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--samples", type=Path, help="private JSON sample manifest")
    sources.add_argument("--html", type=Path, help="already saved private TASK-033 HTML")
    parser.add_argument("--expect-sha256", help="required with --html")
    args = parser.parse_args(argv)
    if bool(args.html) != bool(args.expect_sha256):
        parser.error("--html and --expect-sha256 must be supplied together")
    private = ROOT / "data/private/tjek"
    try:
        if args.samples:
            samples = read_sample_file(args.samples, private_root=private)
            source = "explicit_sample_manifest"
        elif args.html:
            samples = html_samples(args.html, expected_sha256=args.expect_sha256, private_root=private)
            source = "saved_task033_html_partial_search"
        else:
            samples = [{"publicId": "URg401m0FUzablaD9avIh", "retailer": "KIWI",
                        "product_class": "packaged_branded"}]
            source = "one_owner_reported_offer"
        report = diagnose(samples, live=args.live)
        report["sample_source"] = source
    except AppCacheError as error:
        print(json.dumps({"status": "invalid_input", "error": error.code}), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "invalid_input", "error": "input_error"}), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 1 if report["status"] == "partial_or_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
