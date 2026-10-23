"""Read-only first-party grocery HTML evidence probe.

This inspects public page shape, NOT current shelf prices, accurate EANs,
proven campaign validity or allowed bulk programmatic access. Raw HTML is
stored exclusively in an ignored private data directory.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_HTML_BYTES = 5_000_000
MAX_REPORT_ITEMS = 12
# One request per CLI invocation. Same-domain probes are deliberately spaced.
# An HTTP 429 is a stronger signal: halt the entire domain for at least a day.
HOST_GAP_SECONDS = 60
SESUM_GAP_SECONDS = 300
HTTP_429_BACKOFF_SECONDS = 86_400

@dataclass(frozen=True)
class Source:
    url: str
    allowed_hosts: tuple[str, ...]
    marker_phrases: tuple[str, ...]

SOURCES: dict[str, Source] = {
    "rema_locked": Source("https://www.rema.no/kampanjer/last-pris/",
                          ("www.rema.no", "rema.no"), ("Låst pris", "Kampanjeprodukter")),
    "kiwi_locked": Source("https://kiwi.no/dagligvarer/prislaas",
                          ("kiwi.no", "www.kiwi.no"), ("Prislås",)),
    "coop_prix_weekly": Source("https://www.coop.no/coop-prix",
                                ("www.coop.no",), ("Ukens tilbud", "Les ukens kundeavis")),
    "coop_extra_weekly": Source("https://www.coop.no/extra",
                                 ("www.coop.no",), ("Ukens utvalgte tilbud fra kundeavis",
                                                     "Les ukens kundeavis")),
    "meny_campaigns": Source("https://meny.no/kampanjer",
                              ("meny.no",), ("Bla i ukens kundeavis", "Alle tilbud")),
    "meny_weekly": Source("https://meny.no/varer/tilbud",
                           ("meny.no",), ("Ukens tilbud",)),
    # Four fixed first-party product pages, at most one GET per CLI call,
    # with the existing 60-second per-host pacing and HTTP 429 halt.
    # Source probe stores HTML only: never claims product price validity.
    "meny_oatly_barista_500": Source(
        "https://meny.no/varer/meieri-egg/plantebasert-drikke/havredrikk/havredrikk-7394376619939",
        ("meny.no",), ("Oatly", "3 for 2", "Plukk & miks Oatly produkter")),
    "meny_oatly_vanilla_1000": Source(
        "https://meny.no/varer/meieri-egg/plantebasert-drikke/havredrikk/havredrikk-ikaffe-7394376622809",
        ("meny.no",), ("Oatly", "3 for 2", "Plukk & miks Oatly produkter")),
    "meny_oatly_churros_1000": Source(
        "https://meny.no/varer/meieri-egg/plantebasert-drikke/havredrikk/havredrikk-ikaffe-7394376623912",
        ("meny.no",), ("Oatly", "3 for 2", "Plukk & miks Oatly produkter")),
    "meny_oatly_chocolate_250": Source(
        "https://meny.no/varer/meieri-egg/plantebasert-drikke/havredrikk/havredrikk-7394376614804",
        ("meny.no",), ("Oatly", "3 for 2", "Plukk & miks Oatly produkter")),
    "sesum_weekly": Source("https://www.sesum.no/ukens-avis",
                           ("www.sesum.no", "sesum.no"), ("Ukens avis", "Uke")),
    "sesum_product": Source(
        "https://www.sesum.no/produkt/10528-cottage-cheese-mager-2-400g-tine",
        ("www.sesum.no", "sesum.no"), ("Priser per kjede", "Cottage Cheese")),
}
EXCERPT_TERMS = re.compile(
    r"\b(kylling|kjøtt|egg|ost|laks|cottage|torsk|karbonade|protein)\b",
    re.I,
)
CURRENCY_RE = re.compile(r"\b\d{1,5}[,.]\d{2}\s*kr\b", re.I)
UNIT_RE = re.compile(r"\b(?:pr\.?|per)\s*(?:kg|l|stk)\b", re.I)
# The first-party Coop pages can serve skeletons until client-side content loads.
# A placeholder is never a named offer even if rendered inside an H3 element.
COOP_WEEKLY_SOURCES = frozenset(("coop_prix_weekly", "coop_extra_weekly"))
MENY_WEEKLY_SOURCES = frozenset(("meny_campaigns", "meny_weekly"))
MENY_LOADING_MARKERS = ("Laster produkter", "Laster kategorier")
PLACEHOLDER_BLOCKS = frozenset("▆▇█░▒▓")

def _placeholder_heading(text: str) -> bool:
    content = "".join(character for character in text if not character.isspace())
    return bool(content) and all(character in PLACEHOLDER_BLOCKS for character in content)


class SourceProbeError(ValueError):
    """Read-only source probe could not prove a safe public HTML response."""

def _text(value: str) -> str:
    return " ".join(value.split())

def validate_url(source: Source, url: str) -> None:
    parsed = urlsplit(url)
    expected = urlsplit(source.url)
    if (parsed.scheme != "https" or parsed.hostname not in source.allowed_hosts
            or parsed.username or parsed.password or parsed.port is not None
            or parsed.path.rstrip("/") != expected.path.rstrip("/")
            or parsed.query or parsed.fragment):
        raise SourceProbeError("redirect outside pinned first-party page")

class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible: list[str] = []
        self.headings: list[tuple[str, str]] = []
        self._heading: str | None = None
        self._heading_parts: list[str] = []
        self._skip: list[str] = []
        self.ldjson_count = 0
        self.title = ""
        self._inside_title = False
        self._title_parts: list[str] = []
        # Keep Coop's real weekly offer-card <article> H3s separate from
        # unrelated recipe, newsletter and navigation H3s.
        self.article_count = 0
        self.article_h3: list[str] = []
        self._article_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)
        if tag in ("script", "style", "svg", "template", "noscript"):
            self._skip.append(tag)
            if tag == "script" and (attrs_dict.get("type") or "").lower() == "application/ld+json":
                self.ldjson_count += 1
            return
        if self._skip:
            return
        if tag == "article":
            self.article_count += 1
            self._article_depth += 1
        if tag == "title":
            self._inside_title = True
            self._title_parts = []
        if tag in ("h1", "h2", "h3"):
            if self._heading is not None:
                # Nested headings are malformed HTML; ignore the older partial.
                self._heading_parts = []
            self._heading = tag
            self._heading_parts = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "svg", "template", "noscript") and self._skip:
            if tag == self._skip[-1]:
                self._skip.pop()
            return
        if self._skip:
            return
        if tag == "article" and self._article_depth:
            self._article_depth -= 1
        if tag == "title" and self._inside_title:
            self.title = _text(" ".join(self._title_parts))
            self._inside_title = False
        if tag == self._heading:
            result = _text(" ".join(self._heading_parts))
            if result:
                self.headings.append((tag, result))
                if tag == "h3" and self._article_depth:
                    self.article_h3.append(result)
            self._heading, self._heading_parts = None, []

    def handle_data(self, data):
        if self._skip:
            return
        if not (value := _text(data)):
            return
        if self._inside_title:
            self._title_parts.append(value)
            return  # Page title is metadata, not body-price evidence.
        self.visible.append(value)
        if self._heading is not None:
            self._heading_parts.append(value)

def analyze_html(source_name: str, payload: bytes, *, final_url: str | None = None,
                 fetched_at: str | None = None, retrieval: str = "saved_html") -> dict:
    if source_name not in SOURCES:
        raise SourceProbeError("unknown pinned source")
    source = SOURCES[source_name]
    validate_url(source, final_url or source.url)
    if not payload or len(payload) > MAX_HTML_BYTES:
        raise SourceProbeError("missing or oversized HTML")
    try:
        html = payload.decode("utf-8-sig")
    except UnicodeError as exc:
        raise SourceProbeError("HTML was not UTF-8") from exc
    if "<html" not in html.lower() and "<!doctype html" not in html.lower():
        raise SourceProbeError("response does not look like HTML")
    reader = _VisibleHTML()
    reader.feed(html)
    reader.close()
    visible = _text(" ".join(reader.visible))
    headings = [{"level": level, "text": value} for level, value in reader.headings]
    snippets: list[str] = []
    for match in EXCERPT_TERMS.finditer(visible):
        excerpt = visible[max(0, match.start() - 65):match.end() + 95]
        if excerpt not in snippets:
            snippets.append(excerpt)
        if len(snippets) >= MAX_REPORT_ITEMS:
            break
    markers = [m for m in source.marker_phrases if m.casefold() in visible.casefold()]
    # Coop homepages contain newsletter, recipe and footer H3s even if the
    # weekly offer cards remain loading skeletons. Only <article> H3s
    # contribute to preliminary card readiness; even these are NOT prices.
    is_coop = source_name in COOP_WEEKLY_SOURCES
    coop_headings = ([h for h in reader.article_h3 if not _placeholder_heading(h)]
                     if is_coop else None)
    coop_skeleton_count = (sum(_placeholder_heading(h) for h in reader.article_h3)
                           if is_coop else None)
    coop_needs_render = (not coop_headings or bool(coop_skeleton_count)
                         if is_coop else None)
    # MENY's catalogue is postcode-dependent. Its weekly pages can include
    # editorial teasers and unrelated headings while products still load.
    # Do not treat site-wide text, price snippets or H3 headings as verified
    # offer-card associations; report article H3s as structure evidence only.
    is_meny = source_name in MENY_WEEKLY_SOURCES
    meny_loader_markers = ([value for value in MENY_LOADING_MARKERS
                            if value.casefold() in visible.casefold()]
                           if is_meny else None)
    meny_article_h3 = ([h for h in reader.article_h3 if not _placeholder_heading(h)]
                       if is_meny else None)
    meny_postcode_prompt = ("postnummer" in visible.casefold() if is_meny else None)
    meny_needs_render = (bool(meny_loader_markers) or not meny_article_h3
                         or source_name == "meny_campaigns" if is_meny else None)
    evidence_state = ("structure_only_needs_review"
                      if ((is_coop and coop_needs_render)
                          or (is_meny and meny_needs_render))
                      else ("visible_text_needs_review" if markers and len(visible) > 20
                            else "insufficient_visible_text"))
    return {
        "source": source_name,
        "source_url": source.url,
        "final_url": final_url or source.url,
        "retrieval": retrieval,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "html_bytes": len(payload),
        "page_title": reader.title,
        "marker_phrases_found": markers,
        "marker_phrases_expected": list(source.marker_phrases),
        "visible_characters": len(visible),
        "heading_counts": {level: sum(h["level"] == level for h in headings)
                           for level in ("h1", "h2", "h3")},
        "headings_sample": headings[:MAX_REPORT_ITEMS],
        "product_heading_sample": (coop_headings[:MAX_REPORT_ITEMS] if is_coop
                                   else meny_article_h3[:MAX_REPORT_ITEMS] if is_meny
                                   else [h["text"] for h in headings
                                         if h["level"] == "h3"
                                         and not _placeholder_heading(h["text"])][:MAX_REPORT_ITEMS]),
        "coop_non_placeholder_h3_sample": (coop_headings[:MAX_REPORT_ITEMS]
                                           if coop_headings is not None else None),
        "coop_article_count": reader.article_count if is_coop else None,
        "coop_placeholder_article_h3_count": coop_skeleton_count,
        "coop_requires_render_review": coop_needs_render,
        "meny_article_h3_count": len(meny_article_h3) if is_meny else None,
        "meny_article_h3_sample": meny_article_h3[:MAX_REPORT_ITEMS] if is_meny else None,
        "meny_loader_phrases_found": meny_loader_markers,
        "meny_postcode_prompt_visible": meny_postcode_prompt,
        "meny_requires_render_review": meny_needs_render,
        "meny_local_store_price_verification": "none" if is_meny else None,
        "currency_text_mentions": len(CURRENCY_RE.findall(visible)),
        "unit_price_text_mentions": len(UNIT_RE.findall(visible)),
        "jsonld_script_count": reader.ldjson_count,
        "keyword_excerpts": snippets,
        "evidence_state": evidence_state,
        "price_verification": "none",
        "ean_verification": "none",
    }

def save_private_snapshot(payload: bytes, report: dict, root: Path) -> tuple[Path, Path]:
    """Immutable hash-named original + report; repeated content is idempotent."""
    name = report["source"]
    digest = report["sha256"]
    if name not in SOURCES or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise SourceProbeError("invalid source or digest")
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    html_file = directory / (digest + ".html")
    report_file = directory / (digest + ".json")
    if html_file.exists() and hashlib.sha256(html_file.read_bytes()).hexdigest() != digest:
        raise SourceProbeError("existing snapshot is inconsistent; refusing overwrite")
    if not html_file.exists():
        html_file.write_bytes(payload)
    if not report_file.exists():
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    return html_file, report_file

def fetch_once(source_name: str) -> tuple[bytes, str]:
    if source_name not in SOURCES:
        raise SourceProbeError("unknown pinned source")
    source = SOURCES[source_name]
    req = Request(source.url, headers={"User-Agent": "NutriValue-public-source-probe/0.1",
                                      "Accept": "text/html"})
    # No cookies, account login, keys, proxy, browser, or automatic retries.
    with urlopen(req, timeout=20) as response:
        final_url = response.geturl()
        validate_url(source, final_url)
        if response.status != 200:
            raise SourceProbeError("source HTTP status was not 200")
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            raise SourceProbeError("source did not return text/html")
        payload = response.read(MAX_HTML_BYTES + 1)
        if len(payload) > MAX_HTML_BYTES:
            raise SourceProbeError("HTML exceeds bounded download")
        return payload, final_url


def _write_host_state(path: Path, host: str, *, status: str, not_before: float) -> None:
    state = {"host": host, "last_status": status, "not_before_epoch": not_before}
    temp = path.with_name(path.name + ".tmp." + str(os.getpid()))
    try:
        with temp.open("x", encoding="utf-8") as file:
            json.dump(state, file, sort_keys=True)
            file.write("\n")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _read_host_state(path: Path, host: str) -> float:
    if not path.exists():
        return 0.0
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or state.get("host") != host
                or state.get("last_status") not in ("attempt", "http_429")
                or type(state.get("not_before_epoch")) not in (int, float)):
            raise ValueError("invalid host state")
        timestamp = state["not_before_epoch"]
        if not 0 <= timestamp <= 32_503_680_000:
            raise ValueError("invalid host timestamp")
        return float(timestamp)
    except (ValueError, OSError, UnicodeError) as exc:
        raise SourceProbeError("private host pacing state is invalid; no request sent") from exc


def _retry_after_epoch(value: str | None, now: float) -> float:
    if value is None:
        return now
    value = value.strip()
    if re.fullmatch(r"\d{1,12}", value):
        return now + int(value)
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            return now
        return stamp.timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return now


def _epoch_label(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def fetch_spaced(source_name: str, private_root: Path, *, now_epoch: float | None = None
                 ) -> tuple[bytes, str]:
    """At most one GET, guarded by a persistent per-host rate budget.

    The per-host POSIX lock protects across separate CLI processes. On 429 we
    never retry, rotate User-Agent, use proxies or pretend it means success.
    An existing private cooldown must remain intact across new invocations.
    """
    if source_name not in SOURCES:
        raise SourceProbeError("unknown pinned source")
    host = urlsplit(SOURCES[source_name].url).hostname
    if not host:
        raise SourceProbeError("missing pinned host")
    store = private_root / ".host-pacing"
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = store / (host + ".lock")
    state_path = store / (host + ".json")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = time.time() if now_epoch is None else now_epoch
        if type(now) not in (int, float) or not 0 <= now <= 32_503_680_000:
            raise SourceProbeError("invalid clock; no HTTP request made")
        previous = _read_host_state(state_path, host)
        if now < previous:
            raise SourceProbeError(
                f"{host}: deferred until {_epoch_label(previous)} (UTC); "
                "no request sent; do not bypass rate restrictions"
            )
        gap = SESUM_GAP_SECONDS if host.endswith(".sesum.no") else HOST_GAP_SECONDS
        _write_host_state(state_path, host, status="attempt", not_before=now + gap)
        try:
            return fetch_once(source_name)
        except HTTPError as exc:
            if exc.code != 429:
                raise
            hint = _retry_after_epoch(exc.headers.get("Retry-After") if exc.headers else None, now)
            pause = max(now + HTTP_429_BACKOFF_SECONDS, hint)
            _write_host_state(state_path, host, status="http_429", not_before=pause)
            raise SourceProbeError(
                f"{host}: HTTP 429; further requests deferred until "
                f"{_epoch_label(pause)} (UTC); no retry; consider official access"
            ) from None
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
