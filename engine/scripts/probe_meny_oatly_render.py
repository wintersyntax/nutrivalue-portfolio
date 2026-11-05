#!/usr/bin/env python3
"""One-shot, read-only Chromium viability check for a single MENY Oatly page.

No navigation to additional product pages, account login, cookie acceptance,
request interception, retry, service bypass, SQL write, or price verification.
Captures ignored private evidence ONLY. Browser's own page resource requests
are normal browser rendering, not an HTML one-GET source probe.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from nutrivalue.providers.source_probe import SOURCES  # noqa: E402

SOURCE = "meny_oatly_barista_500"
URL = SOURCES[SOURCE].url
PRIVATE_DIR = ROOT / "data/private/render-probes/meny-oatly-barista"
HTML_LIMIT = 5_000_000


def exact_page_url(final: str) -> bool:
    url = urlsplit(final)
    expected = urlsplit(URL)
    return (url.scheme == "https" and url.hostname == expected.hostname
            and url.username is None and url.password is None
            and url.port is None and not url.query and not url.fragment
            and url.path.rstrip("/") == expected.path.rstrip("/"))


def page_signals(title: str, visible: str) -> dict:
    """Conservative visibility diagnostics, never mark retail price verified."""
    normalized = " ".join(visible.split())
    low = normalized.casefold()
    return {
        "title_mentions_oatly": "oatly" in title.casefold(),
        "title_mentions_29_90": "29,90" in title,
        "visible_mentions_oatly": "oatly" in low,
        "visible_mentions_29_90": bool(re.search(r"(?<!\d)29\s*[, ]\s*90\b", normalized)),
        "visible_mentions_campaign": (
            "3 for 2" in low or "plukk & miks" in low),
        "visible_mentions_price_unit": "kr/l" in low,
        "visible_mentions_loading": (
            "laster produkter" in low or "laster kategorier" in low),
        "visible_text_chars": len(normalized),
        "visible_text_excerpt": normalized[:2500],
        "verified_price": False,
        "store_selected": False,
        "product_campaign_price_join": "not_attempted",
    }


def run_once() -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright unavailable in this Python environment") from exc
    if PRIVATE_DIR.exists() and any(PRIVATE_DIR.iterdir()):
        raise RuntimeError("private render evidence already exists; refusing overwrite")
    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                locale="nb-NO", viewport={"width": 1365, "height": 900})
            page = context.new_page()
            response = page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
            # Single top-level navigation, fixed short settling period.
            page.wait_for_timeout(6000)
            final = page.url
            if not exact_page_url(final):
                raise RuntimeError("unexpected product URL or redirect; no evidence saved")
            if response is None or response.status != 200:
                raise RuntimeError(
                    "non-200 main page response; do not retry automatically")
            html = page.content().encode("utf-8")
            if not 0 < len(html) <= HTML_LIMIT:
                raise RuntimeError("rendered HTML absent or oversized")
            screenshot = page.screenshot(full_page=False)
            title = page.title()
            visible = page.locator("body").inner_text(timeout=5000)
            report = {
                "source": SOURCE,
                "requested_url": URL,
                "final_url": final,
                "http_status": response.status,
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "title": title,
                "rendered_html_sha256": hashlib.sha256(html).hexdigest(),
                "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
                "rendered_html_bytes": len(html),
                "frame_count": len(page.frames),
                "signals": page_signals(title, visible),
                "method": "one_browser_navigation_six_second_wait_no_clicks",
                "evidence_state": "browser_render_only_needs_owner_visual_review",
            }
        finally:
            browser.close()
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "rendered.html", "screenshot.png"):
        if (PRIVATE_DIR / name).exists():
            raise RuntimeError("render output already exists; never overwrite")
    (PRIVATE_DIR / "rendered.html").write_bytes(html)
    (PRIVATE_DIR / "screenshot.png").write_bytes(screenshot)
    (PRIVATE_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    try:
        report = run_once()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"MENY OATLY RENDER: RED - {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"PRIVATE EVIDENCE: {PRIVATE_DIR}")
    print("Do not infer a price from title alone or promote any ranking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
