#!/usr/bin/env python3
"""Classify one privately entered flyer headline, without saving or fetching it.

Runs locally; title/description are never included in output. Intended only
for owner manual review after viewing their already saved PRIVATE offers.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrivalue.offer_categories import classify_offer  # noqa: E402


def main() -> int:
    try:
        heading = input("PRIVATE heading (local input only): ")
        description = input("PRIVATE description (optional): ")
        decision = classify_offer(heading, description or None)
    except (ValueError, EOFError) as exc:
        # Do not echo potentially private bad inputs or multiline text.
        print("OFFER CATEGORY REVIEW: invalid or unavailable local input",
              file=sys.stderr)
        return 2
    print(json.dumps(asdict(decision), sort_keys=True))
    print("Local manual classification suggestion only; no SKU or nutrition proof.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
