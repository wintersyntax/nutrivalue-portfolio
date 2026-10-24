"""TASK-033: private, read-only HTML container-shape diagnostics.

Only structural counters for a single already archived eTilbudsavis HTML
response. Never print payload text, titles, prices, URLs or EANs. No
remote requests, source mutation or attempts to reconstruct SKU data.
"""
from __future__ import annotations

import json
import re

from nutrivalue.providers.tjek_site_structured import (
    _Scripts, DATA_ARRAY_START, MAX_HTML_BYTES,
)

NEXT_PUSH = "self.__next_f.push("
# These key names are allowlisted structural indicators, not product facts.
KEYS = (
    "publicId",
    "publicationPublicId",
    "businessPublicId",
    "membershipPrice",
    "pieceCountGet",
    "pieceCountGetFor",
)
MAX_PUSH_SEGMENTS = 600


def diagnose_site_html_shape(html: bytes, *, target_catalog: str) -> dict[str, object]:
    """Return fixed-schema, counts-only structure evidence for saved HTML.

    Next.js flight arrays are decoded ONCE for shape detection; the
    inner strings are not interpreted as offers or published to output.
    This does not assume that page-search is a complete catalogue.
    """
    if not isinstance(html, bytes) or not 0 < len(html) <= MAX_HTML_BYTES:
        raise ValueError("HTML must be nonempty bounded bytes")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,90}", target_catalog):
        raise ValueError("target catalog ID invalid")
    try:
        page = html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("HTML must be UTF-8") from exc
    parser = _Scripts()
    parser.feed(page)
    scripts = parser.scripts

    # Literal payload/decoded stream evidence is deliberately kept to
    # predefined boolean/counter fields; never include the decoded text.
    stream_contents: list[str] = []
    next_decode_ok = 0
    next_decode_failed = 0
    next_count = 0
    for script in scripts:
        start = 0
        while True:
            i = script.find(NEXT_PUSH, start)
            if i < 0:
                break
            next_count += 1
            start = i + len(NEXT_PUSH)
            if next_count > MAX_PUSH_SEGMENTS:
                raise ValueError("too many Next flight segments")
            try:
                parsed, _ = json.JSONDecoder().raw_decode(script[start:].lstrip())
            except (ValueError, json.JSONDecodeError):
                next_decode_failed += 1
                continue
            if not isinstance(parsed, list):
                next_decode_failed += 1
                continue
            next_decode_ok += 1
            if len(parsed) >= 2 and isinstance(parsed[1], str):
                stream_contents.append(parsed[1])

    streamed = "".join(stream_contents)
    script_text = "\n".join(scripts)
    raw_data_starts = len(DATA_ARRAY_START.findall(script_text))
    decoded_data_starts = len(DATA_ARRAY_START.findall(streamed))

    out: dict[str, object] = {
        "state": "archived_html_shape_only",
        "html_bytes": len(html),
        "script_bodies": len(scripts),
        "script_chars": len(script_text),
        "literal_data_array_starts": raw_data_starts,
        "next_f_push_calls": next_count,
        "next_f_decoded_arrays": next_decode_ok,
        "next_f_unrecognized_arrays": next_decode_failed,
        "next_f_string_segments": len(stream_contents),
        "decoded_flight_data_array_starts": decoded_data_starts,
        "page_has_next_data_script": "__NEXT_DATA__" in page,
        "page_has_next_f_marker": NEXT_PUSH in page,
        "catalog_occurrences_in_html": page.count(target_catalog),
        "catalog_occurrences_in_decoded_flight": streamed.count(target_catalog),
        "network_requests": 0,
        "source_only": True,
        "rank_eligible": False,
    }
    for key in KEYS:
        # Fixed labels only; counts may include nonoffer page metadata.
        out[f"raw_marker:{key}"] = script_text.count(key)
        out[f"decoded_flight_marker:{key}"] = streamed.count(key)
    return out
