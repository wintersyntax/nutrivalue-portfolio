#!/usr/bin/env python3
"""TASK-033: ONE private eTilbudsavis public-page HTML capture + schema pilot.

Only an approved Tjek-family page, bounded size, opt-in; saves original
HTML privately even when a currently unsupported Next hydration layout
is observed. Never outputs offer names, IDs, prices or raw HTML.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
# Direct CLI execution sets sys.path[0] to scripts/, not the repository
# root. Add both paths so the shared scripts.* helper can be imported
# without relying on PYTHONPATH=src:. in the owner's terminal.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.tjek_offline import parse_snapshot  # noqa: E402
from nutrivalue.providers.tjek_site_structured import (  # noqa: E402
    MAX_HTML_BYTES, SiteShapeError, extract_structured_offers,
    inspect_site_offers,
)
from scripts.probe_tjek_etilbudsavis_host import _read_original  # noqa: E402

URL = "https://etilbudsavis.no/soek/husholdning"
PRIVATE = ROOT / "data/private/tjek"
TIMEOUT = 15


class PageProbeError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PageProbeError("page redirected; stopped without following")


def _fetch_once(*, opener=None) -> bytes:
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect()).open
    request = urllib.request.Request(
        URL, method="GET",
        headers={"Accept": "text/html",
                 "User-Agent": "NutriValue-personal-Tjek-site-sample/0.1"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            if response.status != 200:
                raise PageProbeError(f"page HTTP {response.status}; no retry")
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if mime != "text/html":
                raise PageProbeError("page MIME not text/html")
            data = response.read(MAX_HTML_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise PageProbeError(f"page HTTP {exc.code}; no retry") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PageProbeError("page network failure; no retry") from None
    if not data or len(data) > MAX_HTML_BYTES:
        raise PageProbeError("page HTML empty or too large")
    return data


def page_probe(*, original: dict, private_root: Path, html: bytes | None = None,
               opener=None, now=None) -> dict:
    """Capture one page or parse a given already saved synthetic fixture."""
    if html is None:
        html = _fetch_once(opener=opener)
    if not isinstance(html, bytes) or not 0 < len(html) <= MAX_HTML_BYTES:
        raise PageProbeError("HTML input invalid")
    root = private_root.resolve(strict=True)
    folder = private_root / "api-view-compare"
    if folder.is_symlink():
        raise PageProbeError("private folder symlink not permitted")
    folder.mkdir(exist_ok=True, mode=0o700)
    folder = folder.resolve(strict=True)
    folder.relative_to(root)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    name = f"etilbudsavis-husholdning-{stamp.strftime('%Y%m%dT%H%M%S%fZ')}.html"
    path = folder / name
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as fp:
        fp.write(html)

    try:
        records = extract_structured_offers(html)
        analysis = inspect_site_offers(original, records,
                                       catalog_id="W8l7eqAB", dealer_id="80742m")
    except SiteShapeError:
        analysis = {
            "status": "saved_html_parser_shape_unrecognized",
            "counters": {}, "rank_eligible": False,
            "source_only": True,
        }
    return {
        "http_requests": 0 if opener is None and html is not None else 1,
        "url_scope": "/soek/husholdning",
        "private_saved_relative": "data/private/tjek/api-view-compare/" + name,
        "sha256": hashlib.sha256(html).hexdigest(),
        **analysis,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="explicit one public-page GET, no redirects or retries")
    parser.add_argument("--original", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live flag required; do not fetch implicitly")
    try:
        original = _read_original(args.original, PRIVATE)
        report = page_probe(original=original, private_root=PRIVATE,
                            opener=urllib.request.build_opener(NoRedirect()).open)
    except (PageProbeError, OSError, ValueError, TypeError) as exc:
        print("SITE STRUCTURED PROBE RED: " +
              (str(exc) if isinstance(exc, PageProbeError)
               else type(exc).__name__), file=sys.stderr)
        return 1
    print("ETILBUDSAVIS PAGE SAMPLE — ONE GET, PRIVATE, SOURCE_ONLY")
    for key, value in report.items():
        if key == "counters":
            for name, count in sorted(value.items()):
                print(f"  {name}={count}")
        else:
            print(f"  {key}: {value}")
    print("Site search results can be PARTIAL. Same Tjek source; "
          "no checkout/price eligibility created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
