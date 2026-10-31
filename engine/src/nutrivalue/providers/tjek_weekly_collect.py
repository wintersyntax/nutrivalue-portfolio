"""TASK-032: bounded, opt-in nine-chain Tjek weekly capture to PRIVATE local files.

TASK-051 adds opt-in catalogue hotspots, per-ID detail recovery of hotspot
offers that the ``/offers`` listing omits, and working page images with a
SHA-256 manifest. Recovered offers are unmodified provider objects stored in
the same TASK-023-compatible envelope; their acquisition path is recorded in
the separate per-chain hotspot file.

This does not schedule network calls or provision cloud resources. Source
payload fields are preserved in TASK-023-compatible JSON envelopes; HTTP
response bytes are not claimed byte-identical to the saved envelopes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from nutrivalue.providers.tjek_offline import (
    CHAINS, MAX_FILE_BYTES, TjekSnapshotError, parse_snapshot,
)

BASE = "https://squid-api.tjek.com/v2"
PAGE_SIZE = 100
MAX_RESPONSE = 1_500_000
MAX_REQUESTS = 80
MAX_DETAIL_REQUESTS = 200
MAX_PAGE_IMAGES = 400
MAX_HOTSPOTS = 2000
MAX_IMAGE_BYTES = 4_000_000
IMAGE_HOST = "image-transformer-api.tjek.com"
MAX_CATALOGS = 2
MAX_PAGES = 5
WEEKLY = re.compile(r"\b(?:uke|week)\s*\d{1,2}\b", re.IGNORECASE)
# Public Oslo centre selects source flyers; not proof for a particular shop.
OSLO_GEO_DEALERS = frozenset(("f5d5lm", "b3e8Fm", "2686gD"))
OSLO_CITY_REFERENCE = (59.9139, 10.7522)


class CollectionError(ValueError):
    """Collection stopped without silently publishing a complete batch."""


def _aware(value: str) -> datetime:
    if not isinstance(value, str):
        raise CollectionError("time must be ISO-8601 with timezone")
    # Tjek sends "+0000"; Python < 3.11 fromisoformat requires "+00:00".
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CollectionError("time must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CollectionError("time must include timezone")
    return parsed


def _week(at: datetime) -> str:
    iso = at.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{2,90}", value):
        raise CollectionError("invalid provider catalog/offer ID")
    return value


def _live_catalogs(body: object, dealer: str, at: datetime) -> list[dict]:
    if not isinstance(body, list) or len(body) > PAGE_SIZE:
        raise CollectionError("catalog response is not a bounded JSON list")
    if len(body) == PAGE_SIZE:
        raise CollectionError("catalog response hits 100-record cap; coverage unknown")
    grouped: dict[tuple, dict] = {}
    for cat in body:
        if not isinstance(cat, dict) or cat.get("dealer_id") != dealer:
            raise CollectionError("catalog response contains foreign/invalid dealer")
        _safe_id(cat.get("id"))
        label = cat.get("label")
        count = cat.get("offer_count")
        if label is not None and not isinstance(label, str):
            raise CollectionError("catalog label is invalid")
        if count is not None and (type(count) is not int or not 0 <= count <= 5000):
            raise CollectionError("catalog declared offer count is invalid")
        start, end = _aware(cat.get("run_from")), _aware(cat.get("run_till"))
        if end <= start:
            raise CollectionError("catalog validity does not increase")
        published = cat.get("publish")
        if published is not None and _aware(published) > at:
            continue
        if not (start <= at < end):
            continue
        if not label or not WEEKLY.search(label) or not count:
            continue
        key = (label.strip().casefold(), start, end)
        best = grouped.get(key)
        if best is None or (count, cat["id"]) > (best["offer_count"], best["id"]):
            grouped[key] = cat
    return sorted(grouped.values(), key=lambda c: (-c["offer_count"], c["id"]))


def _oslo_geo_catalogs(body: object, dealer: str, at: datetime) -> list[dict]:
    """Bounded Oslo-source flyer selection; never assert shop-level applicability."""
    if dealer not in OSLO_GEO_DEALERS:
        raise CollectionError("unsupported Oslo geographic dealer")
    if not isinstance(body, list) or len(body) >= PAGE_SIZE:
        raise CollectionError("Oslo geographic catalog listing invalid or capped")
    local: list[dict] = []
    universal: list[dict] = []
    seen: set[str] = set()
    for cat in body:
        if not isinstance(cat, dict) or cat.get("dealer_id") != dealer:
            raise CollectionError("foreign Oslo geographic catalog")
        cid = _safe_id(cat.get("id"))
        if cid in seen:
            raise CollectionError("duplicate Oslo geographic catalog")
        seen.add(cid)
        count, flag, label = cat.get("offer_count"), cat.get("all_stores"), cat.get("label")
        if type(count) is not int or not 0 <= count <= 5000:
            raise CollectionError("invalid Oslo geographic offer_count")
        if type(flag) is not bool or not isinstance(label, str):
            raise CollectionError("invalid Oslo geographic catalog flags")
        types = cat.get("types")
        if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
            raise CollectionError("invalid Oslo geographic catalog types")
        start, end = _aware(cat.get("run_from")), _aware(cat.get("run_till"))
        if end <= start:
            raise CollectionError("invalid Oslo geographic catalogue validity")
        published = cat.get("publish")
        if published is not None and _aware(published) > at:
            continue
        if not (start <= at < end) or count == 0 or "paged" not in types:
            continue
        # Joker's October recipe book is not a week-42 offer flyer.
        if dealer == "b3e8Fm" and "kundeavis" not in label.casefold():
            continue
        (universal if flag else local).append(cat)
    if len(local) != 1:
        raise CollectionError(
            "Oslo geographic query did not resolve exactly one local weekly flyer")
    return local + sorted(universal, key=lambda c: (-c["offer_count"], c["id"]))


def live_json(path: str, params: dict[str, object], *, timeout: int = 15,
              interval: float = 0.8, opener=urllib.request.urlopen,
              clock=time.monotonic, sleep=time.sleep):
    """One serial identifying read, no retry; 401/403/429 stop upstream."""
    if not (path in ("catalogs", "offers")
            or re.fullmatch(r"catalogs/[A-Za-z0-9_-]{2,90}/(?:hotspots|pages)", path)
            or re.fullmatch(r"offers/[A-Za-z0-9_-]{2,90}", path)):
        raise CollectionError("unsupported Tjek read endpoint")
    if type(timeout) is not int or not 1 <= timeout <= 30:
        raise CollectionError("timeout must be 1..30 seconds")
    if not 0 <= interval <= 5:
        raise CollectionError("interval must be between 0 and 5 seconds")
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{BASE}/{path}" + (f"?{query}" if query else ""),
        headers={"Accept": "application/json", "User-Agent": "NutriValue-private-weekly/0.1"},
        method="GET",
    )
    # Paced serial GETs. Calling code invokes this once per bounded API request.
    last = getattr(live_json, "_last_request", None)
    if last is not None:
        sleep(max(0, interval - (clock() - last)))
    live_json._last_request = clock()
    try:
        with opener(request, timeout=timeout) as response:
            if response.status != 200:
                raise CollectionError(f"Tjek HTTP {response.status}; no retry")
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if mime != "application/json":
                raise CollectionError("Tjek returned non-JSON content type")
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        raise CollectionError(f"Tjek HTTP {exc.code}; no retry") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CollectionError("Tjek network/timeout error; no retry") from exc
    if len(raw) > MAX_RESPONSE:
        raise CollectionError("Tjek JSON response exceeded byte limit")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CollectionError("invalid Tjek JSON response") from exc


def _page(body: object, *, catalog_id: str, dealer: str) -> list[dict]:
    if not isinstance(body, list) or len(body) > PAGE_SIZE:
        raise CollectionError("offer response is not a bounded JSON list")
    for offer in body:
        if not isinstance(offer, dict) or offer.get("dealer_id") != dealer:
            raise CollectionError("offer response contains foreign/invalid dealer")
        _safe_id(offer.get("id"))
        if offer.get("catalog_id") not in (None, catalog_id):
            raise CollectionError("offer belongs to a different catalog")
    return body


def live_bytes(url: str, *, timeout: int = 20, interval: float = 0.3,
               opener=urllib.request.urlopen, clock=time.monotonic,
               sleep=time.sleep) -> bytes:
    """One paced GET of a Tjek page image; no retry, bounded size."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != IMAGE_HOST:
        raise CollectionError("page image URL is not a Tjek image host")
    last = getattr(live_bytes, "_last_request", None)
    if last is not None:
        sleep(max(0, interval - (clock() - last)))
    live_bytes._last_request = clock()
    request = urllib.request.Request(
        url, headers={"User-Agent": "NutriValue-private-weekly/0.1"}, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            if response.status != 200:
                raise CollectionError(f"Tjek image HTTP {response.status}; no retry")
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                raise CollectionError("Tjek page image has non-image content type")
            raw = response.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CollectionError(f"Tjek image HTTP {exc.code}; no retry") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CollectionError("Tjek image network/timeout error; no retry") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise CollectionError("Tjek page image empty or over byte limit")
    return raw


def _hotspot_offers(body: object, *, page_count: object) -> dict[str, list[int]]:
    """Map hotspot offer IDs to sorted catalogue page numbers."""
    if not isinstance(body, list) or len(body) > MAX_HOTSPOTS:
        raise CollectionError("hotspot response is not a bounded JSON list")
    limit = page_count if type(page_count) is int and page_count > 0 else 500
    found: dict[str, set[int]] = {}
    for spot in body:
        if not isinstance(spot, dict):
            raise CollectionError("hotspot entry is not an object")
        if spot.get("type") != "offer":
            continue
        offer = spot.get("offer")
        if not isinstance(offer, dict):
            raise CollectionError("offer hotspot lacks offer object")
        oid = _safe_id(offer.get("id"))
        locations = spot.get("locations")
        if not isinstance(locations, dict):
            raise CollectionError("offer hotspot lacks page locations")
        pages = found.setdefault(oid, set())
        for key in locations:
            if not isinstance(key, str) or not key.isdigit() or not 1 <= int(key) <= limit:
                raise CollectionError("hotspot page number is invalid")
            pages.add(int(key))
    return {oid: sorted(pages) for oid, pages in sorted(found.items())}


def _detail(body: object, *, offer_id: str, catalog_id: str, dealer: str) -> dict:
    if not isinstance(body, dict) or body.get("id") != offer_id:
        raise CollectionError("offer detail does not match requested ID")
    if body.get("dealer_id") != dealer or body.get("catalog_id") != catalog_id:
        raise CollectionError("offer detail belongs to another dealer/catalog")
    return body


def _page_urls(body: object, *, page_count: object) -> list[str]:
    if not isinstance(body, list) or len(body) > 500:
        raise CollectionError("catalog pages response is not a bounded JSON list")
    if type(page_count) is int and body and len(body) != page_count:
        raise CollectionError("catalog pages do not match declared page_count")
    urls = []
    for page in body:
        url = page.get("zoom") if isinstance(page, dict) else None
        if not isinstance(url, str):
            raise CollectionError("catalog page lacks zoom image URL")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != IMAGE_HOST:
            raise CollectionError("catalog page image URL is not a Tjek image host")
        urls.append(url)
    return urls


def _private_save(folder: Path, filename: str, contents: bytes) -> None:
    with os.fdopen(os.open(folder / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(contents)


def _envelope(entries: list[dict], fetched_at: str) -> bytes:
    return json.dumps({"fetched_at": fetched_at, "entries": entries}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def _drop_invalid_offers(dealer: str, entries: list[dict], fetched_at: str):
    """TASK-079: when the whole envelope fails validation, test each offer alone
    (with its own catalogue) and drop only the offers that fail. Returns
    (kept entries, rejected [{catalog_id, offer_id, error, offer}]). A catalogue-
    level problem rejects every offer, which the caller treats as before."""
    kept, rejected = [], []
    for entry in entries:
        good = []
        for offer in entry["offers"]:
            probe = _envelope([{"catalog": entry["catalog"], "offers": [offer]}], fetched_at)
            try:
                parse_snapshot(probe, f"{dealer}.json")
                good.append(offer)
            except TjekSnapshotError as exc:
                rejected.append({"catalog_id": entry["catalog"].get("id"),
                                 "offer_id": offer.get("id") if isinstance(offer, dict) else None,
                                 "error": str(exc), "offer": offer})
        kept.append({"catalog": entry["catalog"], "offers": good})
    return kept, rejected


def _snapshot(folder: Path, dealer: str, entries: list[dict],
              *, fetched_at: str) -> tuple[int, str, str, list[dict]]:
    # Preserve empty selected catalogues alongside populated ones: hiding them
    # could make TASK-030's saved-source completeness audit look falsely GREEN.
    if not any(entry["offers"] for entry in entries):
        return 0, "", "", []
    raw = _envelope(entries, fetched_at)
    if len(raw) > MAX_FILE_BYTES:
        raise CollectionError("saved chain envelope exceeds TASK-023 size limit")
    filename = f"{dealer}.json"
    rejected: list[dict] = []
    try:
        snap = parse_snapshot(raw, filename)
    except TjekSnapshotError as first:
        snap = None
        kept, rejected = _drop_invalid_offers(dealer, entries, fetched_at)
        if rejected and any(e["offers"] for e in kept):
            try:
                cleaned = _envelope(kept, fetched_at)
                snap = parse_snapshot(cleaned, filename)
            except TjekSnapshotError:
                snap = None
        if snap is None:
            # Keep failed source fields PRIVATE, distinctly named and not eligible
            # for TASK-023 import, to diagnose a changed upstream schema offline.
            # Parser errors identify fields/validation rules, not offer contents.
            quarantine = f"{dealer}.unvalidated"
            _private_save(folder, quarantine, raw)
            raise CollectionError(
                f"TASK-023 source validation rejected {CHAINS[dealer]}: {first}"
            ) from first
        raw = cleaned
    if rejected:
        _private_save(folder, f"{dealer}.rejected.json", json.dumps({
            "schema_version": 1, "dealer_id": dealer, "fetched_at": fetched_at,
            "source_only": True, "offers": rejected,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    _private_save(folder, filename, raw)
    return len(snap.offers), snap.sha256, filename, rejected



def recover_quarantine(*, archive_root: Path, input_path: Path,
                       apply: bool = False) -> dict:
    """Validate existing private raw-field quarantine without any HTTP.

    Validated bytes may be saved as a NEW immutable .json sibling only
    with --apply; the original error manifest and quarantine are unchanged.
    """
    try:
        root = Path(archive_root).resolve(strict=True)
        source = Path(input_path)
        if source.is_symlink():
            raise CollectionError("quarantine must be a regular private file")
        full = source.resolve(strict=True)
        parts = full.relative_to(root).parts
    except (OSError, ValueError) as exc:
        raise CollectionError("quarantine not within private weekly archive") from exc
    if (len(parts) != 3
            or not re.fullmatch(r"\d{4}-W\d{2}", parts[0])
            or not re.fullmatch(r"run-[A-Za-z0-9_-]{8,40}", parts[1])
            or not parts[2].endswith(".unvalidated")):
        raise CollectionError("expected private YYYY-Www/run-*/dealer.unvalidated")
    dealer = parts[2][:-len(".unvalidated")]
    if dealer not in CHAINS:
        raise CollectionError("quarantine file is not one of nine dealers")
    if not full.is_file():
        raise CollectionError("quarantine must be a regular private file")
    with full.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise CollectionError("quarantine file missing or oversized")
    filename = f"{dealer}.json"
    try:
        snap = parse_snapshot(raw, filename)
    except TjekSnapshotError as exc:
        raise CollectionError(f"offline quarantine still invalid: {exc}") from exc
    if {c[2] for c in snap.catalogs} != {dealer}:
        raise CollectionError("quarantine dealer does not match captured catalog")
    target = full.parent / filename
    if target.exists() or target.is_symlink():
        raise CollectionError("validated sibling exists; never overwrite")
    if apply:
        _private_save(full.parent, filename, raw)
    return {
        "chain": CHAINS[dealer], "dealer_id": dealer,
        "catalogs": len(snap.catalogs), "offers": len(snap.offers),
        "sha256": snap.sha256,
        "status": "validated_saved" if apply else "validated_dry_run",
        "source": "source_only", "http_requests": 0,
        "original_manifest_updated": False,
    }


def collect_week(*, as_of: str, out_dir: Path, get_json,
                 max_requests: int = MAX_REQUESTS,
                 max_catalogs: int = MAX_CATALOGS, max_pages: int = MAX_PAGES,
                 allow_repeat: bool = False, run_id: str | None = None,
                 captured_at: datetime | None = None,
                 extra_store: dict | None = None,
                 hotspots: bool = False,
                 max_detail_requests: int = MAX_DETAIL_REQUESTS,
                 page_images: bool = False, get_bytes=None,
                 max_page_images: int = MAX_PAGE_IMAGES) -> dict:
    """Exactly nine selected dealers; partial attempts are immutable evidence.

    A fake get_json(endpoint, params) gives fully deterministic offline tests.
    The live CLI must pass --live; this function does not schedule itself.
    """
    at = _aware(as_of)
    if type(max_requests) is not int or not 9 <= max_requests <= 100:
        raise CollectionError("max_requests must be between 9 and 100")
    if type(max_catalogs) is not int or not 1 <= max_catalogs <= 3:
        raise CollectionError("max_catalogs must be between 1 and 3")
    if type(max_pages) is not int or not 1 <= max_pages <= 8:
        raise CollectionError("max_pages must be between 1 and 8")
    if type(allow_repeat) is not bool:
        raise CollectionError("allow_repeat must be boolean")
    if type(hotspots) is not bool or type(page_images) is not bool:
        raise CollectionError("hotspots/page_images must be boolean")
    if type(max_detail_requests) is not int or not 0 <= max_detail_requests <= 400:
        raise CollectionError("max_detail_requests must be between 0 and 400")
    if type(max_page_images) is not int or not 0 <= max_page_images <= 600:
        raise CollectionError("max_page_images must be between 0 and 600")
    if page_images and not callable(get_bytes):
        raise CollectionError("page_images requires an image getter")
    if extra_store is not None:
        # The CLI obtains this from the archived PUBLIC Tjek shop listing,
        # never from home coordinates. Revalidate before a network request.
        if not isinstance(extra_store, dict):
            raise CollectionError("Extra shop must be validated public store metadata")
        required = ("store_id", "name", "latitude", "longitude")
        if not all(k in extra_store for k in required):
            raise CollectionError("Extra shop metadata is incomplete")
        _safe_id(extra_store["store_id"])
        if not isinstance(extra_store["name"], str) or not extra_store["name"].strip():
            raise CollectionError("invalid public Extra shop")
        lat, lng = extra_store["latitude"], extra_store["longitude"]
        if (type(lat) not in (float, int) or type(lng) not in (float, int)
                or not 59.7 <= lat <= 60.15 or not 10.4 <= lng <= 11.1):
            raise CollectionError("Extra shop is not in supported Oslo area")
        if extra_store.get("evidence") != "owner-private archived Tjek public shop listing":
            raise CollectionError("Extra location must originate in owner-private Tjek stores listing")
    week_dir = Path(out_dir) / _week(at)
    if week_dir.exists() and not allow_repeat and list(week_dir.glob("run-*/manifest.json")):
        raise CollectionError("weekly capture already attempted; use explicit --allow-repeat")
    stamp = captured_at or datetime.now(timezone.utc)
    stamp = _aware(stamp.isoformat()).astimezone(timezone.utc)
    run_id = run_id or stamp.strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", run_id):
        raise CollectionError("unsafe run identifier")
    folder = week_dir / f"run-{run_id}"
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    fetched_at = stamp.isoformat()
    records = []
    used = 0
    details_used = 0
    images_used = 0
    halted = None

    def request(endpoint, params):
        nonlocal used
        if used >= max_requests:
            raise CollectionError("HTTP request budget exhausted")
        used += 1
        return get_json(endpoint, params)

    try:
        for dealer, name in CHAINS.items():
            entries: list[dict] = []
            spot_catalogs: list[dict] = []
            page_jobs: list[tuple[str, list[str]]] = []
            listing_total = hotspot_total = recovered_total = 0
            selection_capped = False
            paging_capped = False
            shortfall = 0
            problem = None
            try:
                params = {"dealer_ids": dealer, "limit": PAGE_SIZE}
                if dealer == "80742m" and extra_store is not None:
                    params.update({"r_lat": extra_store["latitude"],
                                   "r_lng": extra_store["longitude"]})
                    # Regional Extra catalog labels are empty. Geographic
                    # provider filtering chooses the relevant offer-bearing
                    # flyer; a seasonal global magazine remains excluded.
                    from nutrivalue.providers.tjek_geo_region import _candidates
                    raw_catalogs = request("catalogs", params)
                    matched, _ = _candidates(raw_catalogs, as_of=as_of)
                    if len(matched) != 1:
                        raise CollectionError(
                            "public Extra shop geographic query did not resolve exactly one active local flyer")
                    catalogs = [c for c in raw_catalogs if c["id"] in matched]
                elif dealer in OSLO_GEO_DEALERS:
                    params.update({"r_lat": OSLO_CITY_REFERENCE[0],
                                   "r_lng": OSLO_CITY_REFERENCE[1]})
                    catalogs = _oslo_geo_catalogs(
                        request("catalogs", params), dealer, at)
                else:
                    catalogs = _live_catalogs(
                        request("catalogs", params), dealer, at)
                if len(catalogs) > max_catalogs:
                    selection_capped = True
                    catalogs = catalogs[:max_catalogs]
                for catalog in catalogs:
                    entry = {"catalog": catalog, "offers": []}
                    entries.append(entry)
                    seen = set()
                    terminal = False
                    for page in range(max_pages):
                        offset = page * PAGE_SIZE
                        offers = _page(request("offers", {
                            "catalog_id": catalog["id"], "limit": PAGE_SIZE, "offset": offset,
                        }), catalog_id=catalog["id"], dealer=dealer)
                        for offer in offers:
                            if offer["id"] in seen:
                                raise CollectionError("duplicate offer across pages")
                            seen.add(offer["id"])
                        entry["offers"].extend(offers)
                        if len(offers) < PAGE_SIZE:
                            terminal = True
                            break
                    if not terminal:
                        paging_capped = True
                    listing_total += len(entry["offers"])
                    if hotspots:
                        cid = catalog["id"]
                        locations: dict[str, object] = {}
                        raw_spots = request(f"catalogs/{cid}/hotspots", {})
                        spots = _hotspot_offers(raw_spots, page_count=catalog.get("page_count"))
                        for spot in raw_spots:
                            if isinstance(spot, dict) and spot.get("type") == "offer":
                                locations.setdefault(spot["offer"]["id"], spot["locations"])
                        recovered = []
                        for oid in spots:
                            if oid in seen:
                                continue
                            if details_used >= max_detail_requests:
                                raise CollectionError("offer detail request budget exhausted")
                            details_used += 1
                            recovered.append(_detail(
                                get_json(f"offers/{oid}", {}), offer_id=oid,
                                catalog_id=cid, dealer=dealer))
                            seen.add(oid)
                        entry["offers"].extend(recovered)
                        hotspot_total += len(spots)
                        recovered_total += len(recovered)
                        spot_catalogs.append({
                            "catalog_id": cid,
                            "listing_offer_ids": sorted(
                                o["id"] for o in entry["offers"]
                                if o["id"] not in {r["id"] for r in recovered}),
                            "detail_recovered_ids": [r["id"] for r in recovered],
                            "hotspot_offer_pages": spots,
                            "hotspot_offer_locations": locations,
                        })
                    if page_images:
                        cid = catalog["id"]
                        page_jobs.append((cid, _page_urls(
                            request(f"catalogs/{cid}/pages", {}),
                            page_count=catalog.get("page_count"))))
                    shortfall += max(0, catalog["offer_count"] - len(entry["offers"]))
            except CollectionError as exc:
                problem = str(exc)
                halted = problem

            rejected = []
            try:
                count, digest, filename, rejected = _snapshot(folder, dealer, entries,
                                                              fetched_at=fetched_at)
            except CollectionError as exc:
                count, digest, filename = 0, "", ""
                problem = str(exc)
                # Only a transport/schema/request-budget failure during HTTP
                # fetch halts other chains. Local TASK-023 validation failure
                # is chain-specific; retain quarantine and continue safely.

            if rejected:
                # The hotspot file must account for exactly the saved offers.
                dropped = {(r["catalog_id"], r["offer_id"]) for r in rejected}
                for cat in spot_catalogs:
                    for key in ("listing_offer_ids", "detail_recovered_ids"):
                        cat[key] = [o for o in cat[key]
                                    if (cat["catalog_id"], o) not in dropped]
            spot_file = None
            if spot_catalogs:
                spot_file = f"{dealer}.hotspots.json"
                _private_save(folder, spot_file, json.dumps({
                    "schema_version": 1, "fetched_at": fetched_at,
                    "dealer_id": dealer, "source_only": True,
                    "catalogs": spot_catalogs,
                }, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            images_saved = 0
            pages_listed = sum(len(urls) for _, urls in page_jobs)
            image_problem = None
            page_file = None
            if page_jobs:
                saved_pages = []
                try:
                    for cid, urls in page_jobs:
                        target = folder / "pages" / dealer / cid
                        target.mkdir(mode=0o700, parents=True, exist_ok=False)
                        for number, url in enumerate(urls, 1):
                            if images_used >= max_page_images:
                                raise CollectionError("page image budget exhausted")
                            images_used += 1
                            raw = get_bytes(url)
                            if not isinstance(raw, bytes) or not raw:
                                raise CollectionError("page image getter returned no bytes")
                            ext = (".jpg" if raw[:3] == b"\xff\xd8\xff" else
                                   ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else
                                   ".webp" if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" else
                                   None)
                            if ext is None:
                                raise CollectionError("page image is not JPEG/PNG/WebP")
                            name_ = f"p{number:03d}{ext}"
                            _private_save(target, name_, raw)
                            images_saved += 1
                            saved_pages.append({
                                "catalog_id": cid, "page": number,
                                "file": f"pages/{dealer}/{cid}/{name_}",
                                "bytes": len(raw),
                                "sha256": hashlib.sha256(raw).hexdigest(),
                            })
                except (CollectionError, OSError) as exc:
                    image_problem = str(exc)
                page_file = f"{dealer}.pages.json"
                _private_save(folder, page_file, json.dumps({
                    "schema_version": 1, "fetched_at": fetched_at,
                    "dealer_id": dealer, "working_copy": True,
                    "complete": image_problem is None, "pages": saved_pages,
                }, sort_keys=True).encode("utf-8"))

            if problem:
                status = "partial_error" if count else "error"
            elif not entries:
                status = "missing_weekly_catalog"
            elif not count:
                status = "no_available_offers"
            elif rejected:
                status = "offers_rejected"
            elif selection_capped:
                status = "catalog_selection_capped"
            elif paging_capped:
                status = "offer_paging_capped"
            elif shortfall:
                status = "source_count_shortfall"
            else:
                status = "candidate_source_count_match"
            record = {
                "dealer_id": dealer, "chain": name, "status": status,
                "catalogs_selected": len(entries), "offers_saved": count,
                "source_count_shortfall": shortfall,
                "snapshot_sha256": digest or None, "snapshot_file": filename or None,
                "validation_error": problem if problem and not halted else None,
                "offers_rejected": len(rejected),
                "rejected_file": f"{dealer}.rejected.json" if rejected else None,
                "rejected_reasons": sorted({r["error"] for r in rejected}),
                "quarantine_file": f"{dealer}.unvalidated"
                    if (folder / f"{dealer}.unvalidated").is_file() else None,
            }
            if hotspots:
                record.update({
                    "listing_offers": listing_total, "hotspot_offers": hotspot_total,
                    "detail_recovered": recovered_total, "hotspot_file": spot_file,
                })
            if page_images:
                record.update({
                    "pages_listed": pages_listed, "page_images_saved": images_saved,
                    "page_image_error": image_problem, "pages_file": page_file,
                })
            if dealer == "80742m" and extra_store is not None:
                # PRIVATE manifest only, never an assertion of verified shelf
                # prices or of a vendor-certified Coop regional mapping.
                record["extra_public_store_geo"] = {
                    "store_id": extra_store["store_id"],
                    "shop_name": extra_store["name"],
                    "catalog_id": entries[0]["catalog"]["id"] if entries else None,
                    "selection": "Tjek /v2/catalogs at public store coordinates",
                    "source_only": True, "verified_shelf_price": False,
                }
            if dealer in OSLO_GEO_DEALERS:
                record["oslo_city_reference_geo"] = {
                    "selection": "Tjek /v2/catalogs at public Oslo city-centre coordinates",
                    "catalog_ids": [entry["catalog"]["id"] for entry in entries],
                    "specific_shop_verified": False,
                    "verified_shelf_price": False,
                    "source_only": True,
                }
            records.append(record)
            if halted:
                break
    finally:
        while len(records) < len(CHAINS):
            dealer, name = list(CHAINS.items())[len(records)]
            records.append({
                "dealer_id": dealer, "chain": name, "status": "not_attempted",
                "catalogs_selected": 0, "offers_saved": 0,
                "source_count_shortfall": 0,
                "snapshot_sha256": None, "snapshot_file": None,
            })
        total = sum(row["offers_saved"] for row in records)
        candidate = sum(row["status"] == "candidate_source_count_match" for row in records)
        report = {
            "schema_version": 1, "scope": "private nine-chain Tjek source_only",
            "as_of": at.isoformat(), "week": _week(at), "fetched_at": fetched_at,
            "run_directory": folder.name,
            "http_requests": used, "request_limit": max_requests,
            "hotspots_enabled": hotspots, "offer_detail_requests": details_used,
            "page_images_enabled": page_images, "page_image_requests": images_used,
            "required_chains": len(CHAINS), "candidate_chains": candidate,
            "total_saved_offers": total,
            "batch_status": "candidate_complete" if candidate == len(CHAINS)
                            else "partial_or_unproven",
            "halted_reason": halted, "chains": records,
            "extra_store_geographic_selection": extra_store is not None,
            "disclaimer": "Advertised chain-level source only, never checked shop prices. "
                          "Even source-count match does not prove full provider availability.",
        }
        _private_save(folder, "manifest.json", json.dumps(
            report, sort_keys=True, indent=2).encode("utf-8"))
    return report
